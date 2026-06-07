"""
Run lifecycle: POST /runs/, idempotency, retry, polling, ingest-on-success.

Uses a fake JobRunner so the tests are deterministic and need no real
pandda2.analyse — they exercise the contract (model + endpoints + serializer),
the dispatch wiring, failure classification, and the ingest-on-success path
(which reuses the existing reconcile, so a run's output lands as Datasets/Events
exactly like a CLI ingest).
"""
import csv
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inspect_api.jobs import JobSpec, LocalProcessRunner
from inspect_api.models import Event, Project, Run
from inspect_api import runservice


class FakeRunner:
    """A controllable JobRunner stand-in (no subprocess)."""

    def __init__(self, state="running"):
        self.state = state
        self.submitted = []

    def probe(self):
        return {"available": True}

    def submit(self, spec, workdir):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        self.submitted.append((spec, workdir))
        return str(workdir)  # handle == workdir, like LocalProcessRunner

    def status(self, handle):
        return {"state": self.state, "exit_code": 0, "outputs": {}}

    def cancel(self, handle):
        self.state = "failed"


def _write_pandda2_tree(out_dir: Path, dtag="x-001"):
    """Minimal but valid PanDDA2 out_dir the ingestor accepts."""
    analyses = out_dir / "analyses"
    analyses.mkdir(parents=True)
    (out_dir / "processed_datasets" / dtag).mkdir(parents=True)
    with open(analyses / "pandda_analyse_events.csv", "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "dtag", "event_idx", "site_idx", "x", "y", "z", "bdc",
            "1-BDC", "analysed_resolution",
        ])
        w.writeheader()
        w.writerow({
            "dtag": dtag, "event_idx": "1", "site_idx": "1",
            "x": "1.0", "y": "2.0", "z": "3.0", "bdc": "0.3",
            "1-BDC": "0.7", "analysed_resolution": "1.8",
        })


class RunArgvTest(TestCase):
    def test_pandda2_analyse_argv(self):
        spec = JobSpec(
            tool="pandda2.analyse",
            inputs={"data_dirs": "/share/in/datasets"},
            params={"out_dir": "/share/out", "local_cpus": 8},
        )
        argv, stdin = LocalProcessRunner()._build_argv(spec, Path("/wd"))
        self.assertEqual(stdin, "")
        self.assertEqual(argv[0], "pandda2.analyse")
        self.assertIn("--data_dirs", argv)
        self.assertEqual(argv[argv.index("--data_dirs") + 1],
                         "/share/in/datasets")
        self.assertEqual(argv[argv.index("--out_dir") + 1], "/share/out")
        self.assertEqual(argv[argv.index("--local_cpus") + 1], "8")
        # The regexes that defeat PanDDA2's protein-grabbing defaults.
        self.assertIn("--pdb_regex", argv)
        self.assertIn("--ligand_cif_regex", argv)

    def test_builder_omits_local_cpus_when_absent(self):
        from inspect_api.jobs import build_pandda2_argv
        argv = build_pandda2_argv(JobSpec(
            tool="pandda2.analyse", inputs={"data_dirs": "/in"},
            params={"out_dir": "/out"}))
        self.assertNotIn("--local_cpus", argv)  # → PanDDA2's own default

    def test_local_runner_defaults_cpus_to_one(self):
        argv, _ = LocalProcessRunner()._build_argv(JobSpec(
            tool="pandda2.analyse", inputs={"data_dirs": "/in"},
            params={"out_dir": "/out"}), Path("/wd"))
        self.assertEqual(argv[argv.index("--local_cpus") + 1], "1")


class ClassifyFailureTest(TestCase):
    def test_oom(self):
        code, _ = runservice.classify_failure("...\nMemoryError\n")
        self.assertEqual(code, "oom")

    def test_free_r(self):
        code, _ = runservice.classify_failure("No RFree Flag found!")
        self.assertEqual(code, "free_r_label")

    def test_unclassified(self):
        code, _ = runservice.classify_failure("something weird")
        self.assertEqual(code, "unclassified_crash")


class ParseProgressTest(TestCase):
    def test_latest_dataset_line_wins(self):
        log = (
            "starting\nPANDDA_PROGRESS: dataset 1/120\n"
            "...\nPANDDA_PROGRESS: dataset 7/120\n...\n"
        )
        self.assertEqual(runservice.parse_progress(log), "dataset 7/120")

    def test_none_yet(self):
        self.assertEqual(runservice.parse_progress("warming up\n"), "")


@override_settings(REINSPECT_UI_BASE_URL="https://reinspect.test")
class RunApiTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tmp = tempfile.mkdtemp()
        self.share = str(Path(self.tmp) / "pandda_inputs" / "grp")
        Path(self.share, "datasets").mkdir(parents=True)
        self.body = {
            "project": "CDK4", "group": "grp", "share_path": self.share,
            "input_hash": "h1",
        }

    def _patch_runner(self, runner):
        return mock.patch.object(runservice, "get_runner",
                                 return_value=runner)

    def test_list_global_and_by_project_id(self):
        fake = FakeRunner()
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            self.client.post("/api/v1/runs/", self.body, format="json")
            other = {**self.body, "project": "BAZ2B", "input_hash": "h2"}
            r2 = self.client.post("/api/v1/runs/", other, format="json")
            pid2 = r2.json()["project_id"]
            # Global list = both runs, newest-first.
            allruns = self.client.get("/api/v1/runs/").json()
            self.assertEqual(allruns["count"], 2)
            self.assertEqual(allruns["results"][0]["project"], "BAZ2B")
            # project_id filter = just that project's run.
            scoped = self.client.get(
                f"/api/v1/runs/?project_id={pid2}"
            ).json()
            self.assertEqual(scoped["count"], 1)
            self.assertEqual(scoped["results"][0]["project"], "BAZ2B")

    def test_allowlisted_params_stored_and_threaded(self):
        fake = FakeRunner()
        body = {**self.body, "params": {"pdb_regex": "custom.pdb",
                                        "local_cpus": "8"}}
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            resp = self.client.post("/api/v1/runs/", body, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["params"]["pdb_regex"], "custom.pdb")
        # Threaded into the JobSpec the runner received (alongside out_dir).
        spec, _ = fake.submitted[0]
        self.assertEqual(spec.params["pdb_regex"], "custom.pdb")
        self.assertEqual(spec.params["local_cpus"], "8")
        self.assertIn("out_dir", spec.params)

    def test_timestamps_are_utc_with_offset(self):
        # USE_TZ=True ⇒ tz-aware UTC, serialized with a "Z" suffix, so the SPA
        # can't misread a naive timestamp as browser-local (inflated durations).
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(FakeRunner()):
            data = self.client.post(
                "/api/v1/runs/", self.body, format="json"
            ).json()
        self.assertTrue(data["submitted_at"].endswith("Z"))

    def test_unknown_param_rejected(self):
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(FakeRunner()):
            resp = self.client.post(
                "/api/v1/runs/",
                {**self.body, "params": {"rm_rf": "/"}},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("rm_rf", resp.json()["detail"])

    def test_create_201_dispatches_and_resolves_project(self):
        fake = FakeRunner()
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            resp = self.client.post("/api/v1/runs/", self.body, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["run_id"])
        self.assertEqual(data["status"], "running")
        self.assertEqual(
            data["ui_url"], f"https://reinspect.test/runs/{data['run_id']}"
        )
        # Project get-or-created by external_id (Materia slug).
        proj = Project.objects.get(external_id="CDK4")
        self.assertEqual(proj.name, "CDK4")
        # project_id lets the run-landing page link to /projects/<id>.
        self.assertEqual(data["project_id"], proj.id)
        # Dispatched a pandda2.analyse spec pointing at the share's datasets.
        spec, _ = fake.submitted[0]
        self.assertEqual(spec.tool, "pandda2.analyse")
        self.assertTrue(spec.inputs["data_dirs"].endswith("/datasets"))

    def test_idempotent_repeat_returns_200_same_run(self):
        fake = FakeRunner()
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            r1 = self.client.post("/api/v1/runs/", self.body, format="json")
            r2 = self.client.post("/api/v1/runs/", self.body, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["run_id"], r2.json()["run_id"])
        self.assertEqual(Run.objects.count(), 1)

    def test_retry_creates_new_run_with_parent(self):
        fake = FakeRunner()
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            r1 = self.client.post("/api/v1/runs/", self.body, format="json")
            rid = r1.json()["run_id"]
            retry_body = {**self.body, "retry_of": int(rid)}
            r2 = self.client.post(
                "/api/v1/runs/", retry_body, format="json"
            )
        self.assertEqual(r2.status_code, 201)
        self.assertNotEqual(r1.json()["run_id"], r2.json()["run_id"])
        self.assertEqual(Run.objects.count(), 2)
        child = Run.objects.get(pk=int(r2.json()["run_id"]))
        self.assertEqual(child.parent_run_id, int(rid))
        # The retry key (<sha256>:retry:<uuid4hex> = 103 chars) must fit the
        # field cap — SQLite ignores it but Postgres enforces it in prod.
        cap = Run._meta.get_field("idempotency_key").max_length
        self.assertLessEqual(len(child.idempotency_key), cap)

    def test_unknown_retry_of_400(self):
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(FakeRunner()):
            resp = self.client.post(
                "/api/v1/runs/", {**self.body, "retry_of": 99999},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_poll_success_ingests_tree(self):
        fake = FakeRunner()
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            rid = self.client.post(
                "/api/v1/runs/", self.body, format="json"
            ).json()["run_id"]
            # The run "succeeds"; produce the expected output tree, then poll.
            run = Run.objects.get(pk=int(rid))
            _write_pandda2_tree(Path(run.out_dir))
            fake.state = "succeeded"
            resp = self.client.get(f"/api/v1/runs/{rid}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "succeeded")
        # Ingest-on-success ran: the run's project now has the event.
        self.assertEqual(
            Event.objects.filter(dataset__project__external_id="CDK4").count(),
            1,
        )

    def test_poll_failure_classified(self):
        fake = FakeRunner()
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            rid = self.client.post(
                "/api/v1/runs/", self.body, format="json"
            ).json()["run_id"]
            run = Run.objects.get(pk=int(rid))
            Path(run.runner_handle, "job.log").write_text(
                "boom\nMemoryError: out of memory\n", encoding="utf-8"
            )
            fake.state = "failed"
            resp = self.client.get(f"/api/v1/runs/{rid}/")
        self.assertEqual(resp.json()["status"], "failed")
        self.assertEqual(resp.json()["failure_mode"], "oom")

    def test_poll_running_populates_progress(self):
        fake = FakeRunner()  # stays "running"
        with override_settings(PANDDA_JOBS_ROOT=self.tmp), \
                self._patch_runner(fake):
            rid = self.client.post(
                "/api/v1/runs/", self.body, format="json"
            ).json()["run_id"]
            run = Run.objects.get(pk=int(rid))
            Path(run.runner_handle, "job.log").write_text(
                "PANDDA_PROGRESS: dataset 1/120\n"
                "PANDDA_PROGRESS: dataset 9/120\n",
                encoding="utf-8",
            )
            resp = self.client.get(f"/api/v1/runs/{rid}/")
        self.assertEqual(resp.json()["status"], "running")
        self.assertEqual(resp.json()["progress"], "dataset 9/120")
