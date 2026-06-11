"""
Deletion & cleanup: the storage-seam delete primitive, the run-delete service
(ownership predicate, orphan check, shared-out_dir guard, audit summary), the
DELETE /runs/ endpoint modes, and the paired submit-side zombie guard.

See docs/DELETION_AND_CLEANUP.md (§3 + §4).
"""
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inspect_api import runservice
from inspect_api.cleanup import CleanupError, delete_run, run_owns_outdir
from inspect_api.models import (
    Artifact, Dataset, Event, Finding, Project, Run, RunDataset,
)
from inspect_api.storage import LocalFileStore


class FakeRunner:
    """Minimal JobRunner stand-in: submit makes the workdir, returns a handle."""

    def probe(self):
        return {"available": True}

    def submit(self, spec, workdir):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        return str(workdir)

    def status(self, handle):
        return {"state": "running", "exit_code": 0, "outputs": {}}

    def cancel(self, handle):
        pass


class StorageDeleteTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_delete_file(self):
        (self.tmp / "a.pdb").write_text("x", encoding="utf-8")
        self.assertTrue(LocalFileStore(self.tmp).delete("a.pdb"))
        self.assertFalse((self.tmp / "a.pdb").exists())

    def test_delete_missing_returns_false(self):
        self.assertFalse(LocalFileStore(self.tmp).delete("nope.pdb"))

    def test_delete_symlink_keeps_target(self):
        data = self.tmp / "data"
        data.mkdir()
        target = data / "real.pdb"
        target.write_text("x", encoding="utf-8")
        (self.tmp / "link.pdb").symlink_to(target)
        self.assertTrue(LocalFileStore(self.tmp).delete("link.pdb"))
        self.assertFalse((self.tmp / "link.pdb").is_symlink())
        self.assertTrue(target.exists())  # the IMPORTED target is untouched

    def test_delete_guards_traversal(self):
        with self.assertRaises(ValueError):
            LocalFileStore(self.tmp).delete("../escape.pdb")


class _RunFixtureMixin:
    """Builds a terminal, Reinspect-owned run with a real out_dir on disk."""

    def _build(self, *, runner_handle="handle", status=Run.Status.SUCCEEDED,
               with_dataset_artifact=False, key="k1", name="P"):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out_dir = tmp / "pandda_results"
        (out_dir / "processed_datasets" / "ds1").mkdir(parents=True)
        project = Project.objects.create(name=name, source_root=str(out_dir))
        run = Run.objects.create(
            project=project, group="g", share_path=str(tmp),
            out_dir=str(out_dir), idempotency_key=key, status=status,
            runner_handle=runner_handle,
        )
        ds = Dataset.objects.create(project=project, dtag="ds1")
        rd = RunDataset.objects.create(run=run, dataset=ds)
        ev = Event.objects.create(dataset=ds, run_dataset=rd, event_num=1)
        map_rel = "processed_datasets/ds1/ds1-event_1_map.ccp4"
        (out_dir / map_rel).write_text("map-bytes", encoding="utf-8")
        Artifact.objects.create(
            event=ev, kind=Artifact.Kind.EVENT_MAP, relpath=map_rel,
            origin=Artifact.Origin.IMPORTED,
        )
        if with_dataset_artifact:
            pdb_rel = "processed_datasets/ds1/ds1-pandda-input.pdb"
            (out_dir / pdb_rel).write_text("pdb", encoding="utf-8")
            Artifact.objects.create(
                dataset=ds, kind=Artifact.Kind.STRUCTURE, relpath=pdb_rel,
                origin=Artifact.Origin.IMPORTED,
            )
        return run, out_dir, ds


class RunOwnershipTests(TestCase):
    def _run(self, **kw):
        p = Project.objects.create(name=kw.pop("name"), source_root="/x")
        defaults = dict(
            project=p, group="g", share_path="/x",
            idempotency_key=kw.pop("key"), status=Run.Status.SUCCEEDED,
            runner_handle="h",
        )
        defaults.update(kw)
        return Run.objects.create(**defaults)

    def test_owned_when_handle_and_terminal(self):
        self.assertTrue(run_owns_outdir(self._run(name="A", key="a")))

    def test_not_owned_without_handle(self):
        run = self._run(name="B", key="b", runner_handle="")
        self.assertFalse(run_owns_outdir(run))

    def test_not_owned_when_running(self):
        run = self._run(name="C", key="c", status=Run.Status.RUNNING)
        self.assertFalse(run_owns_outdir(run))


class DeleteRunServiceTests(_RunFixtureMixin, TestCase):
    def test_db_only_keeps_outdir(self):
        run, out_dir, ds = self._build()
        summary = delete_run(run, delete_outdir=False)
        self.assertFalse(Run.objects.filter(pk=summary["run_id"]).exists())
        self.assertTrue(out_dir.is_dir())  # left on disk for manual cleanup
        self.assertEqual(summary["events_deleted"], 1)
        self.assertEqual(summary["artifacts_deleted"], 1)
        self.assertFalse(summary["out_dir_removed"])
        self.assertEqual(summary["out_dir"], str(out_dir))

    def test_safe_delete_removes_owned_tree(self):
        run, out_dir, ds = self._build()
        summary = delete_run(run, delete_outdir=True)
        self.assertTrue(summary["out_dir_removed"])
        self.assertFalse(out_dir.exists())
        self.assertGreater(summary["disk_freed_bytes"], 0)

    def test_refuse_unowned_tree(self):
        # runner_handle="" mimics the in-place-ingest synthetic run.
        run, out_dir, ds = self._build(runner_handle="")
        with self.assertRaises(CleanupError):
            delete_run(run, delete_outdir=True)
        self.assertTrue(Run.objects.filter(pk=run.pk).exists())  # not deleted
        self.assertTrue(out_dir.exists())

    def test_force_removes_unowned_tree(self):
        run, out_dir, ds = self._build(runner_handle="")
        summary = delete_run(run, delete_outdir=True, force=True)
        self.assertTrue(summary["out_dir_removed"])
        self.assertFalse(out_dir.exists())

    def test_refuse_shared_outdir(self):
        run, out_dir, ds = self._build()
        Run.objects.create(
            project=run.project, group="g2", share_path=run.share_path,
            out_dir=str(out_dir), idempotency_key="k2",
            status=Run.Status.SUCCEEDED, runner_handle="h2",
        )
        with self.assertRaises(CleanupError):
            delete_run(run, delete_outdir=True)
        self.assertTrue(out_dir.exists())

    def test_refuse_orphan_dataset_artifact(self):
        run, out_dir, ds = self._build(with_dataset_artifact=True)
        # source_root == out_dir (the only on-disk copy of the pdb); no other
        # surviving root holds it → removal would orphan it.
        with override_settings(
            PANDDA_DATA_ROOT=str(out_dir.parent / "noexist")
        ):
            with self.assertRaises(CleanupError):
                delete_run(run, delete_outdir=True)
        self.assertTrue(out_dir.exists())

    def test_orphan_overridden_by_force(self):
        run, out_dir, ds = self._build(with_dataset_artifact=True)
        with override_settings(
            PANDDA_DATA_ROOT=str(out_dir.parent / "noexist")
        ):
            summary = delete_run(run, delete_outdir=True, force=True)
        self.assertTrue(summary["out_dir_removed"])

    def test_no_orphan_when_copy_survives(self):
        run, out_dir, ds = self._build(with_dataset_artifact=True)
        # A surviving second run holds a copy of the dataset artifact.
        other = out_dir.parent / "other_results"
        (other / "processed_datasets" / "ds1").mkdir(parents=True)
        (other / "processed_datasets/ds1/ds1-pandda-input.pdb").write_text(
            "pdb", encoding="utf-8"
        )
        Run.objects.create(
            project=run.project, group="g2", share_path=run.share_path,
            out_dir=str(other), idempotency_key="k2",
            status=Run.Status.SUCCEEDED, runner_handle="h2",
        )
        summary = delete_run(run, delete_outdir=True)
        self.assertTrue(summary["out_dir_removed"])

    def test_finding_and_crystal_survive(self):
        run, out_dir, ds = self._build()
        finding = Finding.objects.create(dataset=ds, centroid=[1.0, 2.0, 3.0])
        ev = Event.objects.get(run_dataset__run=run)
        ev.finding = finding
        ev.save(update_fields=["finding"])
        delete_run(run, delete_outdir=True)
        self.assertTrue(Finding.objects.filter(pk=finding.pk).exists())
        self.assertTrue(Dataset.objects.filter(pk=ds.pk).exists())


class RunDeleteEndpointTests(_RunFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_delete_default_db_only(self):
        run, out_dir, ds = self._build()
        resp = self.client.delete(f"/api/v1/runs/{run.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Run.objects.filter(pk=run.id).exists())
        self.assertTrue(out_dir.is_dir())
        self.assertEqual(resp.json()["events_deleted"], 1)
        self.assertFalse(resp.json()["out_dir_removed"])

    def test_delete_true_removes_tree(self):
        run, out_dir, ds = self._build()
        resp = self.client.delete(
            f"/api/v1/runs/{run.id}/?delete_outdir=true"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["out_dir_removed"])
        self.assertFalse(out_dir.exists())

    def test_delete_bad_mode_400(self):
        run, out_dir, ds = self._build()
        resp = self.client.delete(
            f"/api/v1/runs/{run.id}/?delete_outdir=maybe"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Run.objects.filter(pk=run.id).exists())

    def test_delete_refusal_400(self):
        run, out_dir, ds = self._build(runner_handle="")
        resp = self.client.delete(
            f"/api/v1/runs/{run.id}/?delete_outdir=true"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Run.objects.filter(pk=run.id).exists())
        self.assertTrue(out_dir.exists())


class ZombieGuardTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.share = str(self.tmp / "pandda_inputs" / "grp")
        Path(self.share, "datasets").mkdir(parents=True)
        (self.tmp / "pandda_results").mkdir()  # the out_dir parent

    def _submit(self, **kw):
        defaults = dict(
            project_external_id="P", group="grp", share_path=self.share,
            input_hash="h",
        )
        defaults.update(kw)
        with override_settings(PANDDA_JOBS_ROOT=str(self.tmp)), \
                mock.patch.object(
                    runservice, "get_runner", return_value=FakeRunner()
                ):
            return runservice.submit_run(**defaults)

    def test_zombie_outdir_refused(self):
        out_dir = self.tmp / "pandda_results" / "grp"
        out_dir.mkdir(parents=True)
        (out_dir / "stale.csv").write_text("x", encoding="utf-8")
        with self.assertRaises(runservice.RunError) as cm:
            self._submit()
        self.assertIn("owned by no run", str(cm.exception))

    def test_populated_outdir_owned_by_run_allowed(self):
        out_dir = self.tmp / "pandda_results" / "grp"
        out_dir.mkdir(parents=True)
        (out_dir / "events.csv").write_text("x", encoding="utf-8")
        p = Project.objects.create(
            name="P", external_id="P", source_root=self.share
        )
        Run.objects.create(
            project=p, group="grp", share_path=self.share,
            out_dir=str(out_dir), idempotency_key="owner",
            status=Run.Status.SUCCEEDED, runner_handle="h",
        )
        run, created = self._submit(input_hash="different")
        self.assertTrue(created)

    def test_empty_outdir_allowed(self):
        run, created = self._submit()
        self.assertTrue(created)
