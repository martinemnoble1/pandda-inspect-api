"""
Tests for the job runner + landing (DESIGN §5), CCP4-free.

The whole submit→poll→land→repoint loop is exercised with a trivial stand-in
"tool" (a tiny executable that writes refine.pdb), an empty activation
prologue, and a temp source_root. This is the tool-agnostic wrapper path from
§5.6: the real giant.quick_refine differs only in the binary + prologue, not
the mechanism.
"""
import tempfile
import time
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from inspect_api.models import Artifact, Dataset, Project


def _make_stand_in_tool(tmp: Path, succeed=True) -> Path:
    """An executable emulating servalcat's CLI shape: it reads the output
    prefix from ``-o <prefix>`` and writes ``<prefix>.pdb``, then exits 0 (1).

    This matches the runner's servalcat argv (DESIGN §5.8) without needing
    CCP4 — the tool-agnostic wrapper path; real servalcat differs only in the
    binary.
    """
    tool = tmp / "fake_refine"
    rc = 0 if succeed else 1
    tool.write_text(
        "#!/bin/sh\n"
        "prefix=\"\"\n"
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in -o) prefix="$2"; shift;; esac\n'
        "  shift\n"
        "done\n"
        '[ -n "$prefix" ] && printf "REFINED" > "${prefix}.pdb"\n'
        f"exit {rc}\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    return tool


def _wait(fn, ok, tries=50, delay=0.1):
    for _ in range(tries):
        v = fn()
        if ok(v):
            return v
        time.sleep(delay)
    return v


class RefinementLoopTests(TestCase):
    def setUp(self):
        # A temp tree that doubles as the project source_root AND jobs root, so
        # resolved input paths + the refined relpath both resolve here.
        self.root = Path(tempfile.mkdtemp())
        self.tooldir = Path(tempfile.mkdtemp())
        # Real on-disk inputs the runner resolves to paths.
        (self.root / "model.pdb").write_text("PDB", encoding="utf-8")
        (self.root / "data.mtz").write_text("MTZ", encoding="utf-8")

        self.project = Project.objects.create(
            name="P", source_root=str(self.root)
        )
        self.dataset = Dataset.objects.create(project=self.project, dtag="d1")
        Artifact.objects.create(
            dataset=self.dataset, kind=Artifact.Kind.STRUCTURE,
            relpath="model.pdb", origin=Artifact.Origin.IMPORTED,
        )
        Artifact.objects.create(
            dataset=self.dataset, kind=Artifact.Kind.DATA_MTZ,
            relpath="data.mtz", origin=Artifact.Origin.IMPORTED,
        )

    def _settings(self, succeed=True):
        tool = _make_stand_in_tool(self.tooldir, succeed=succeed)
        return override_settings(
            CCP4_SETUP_SH="", CONDA_SH="", PANDDA2_CONDA_ENV="",
            REFINE_TOOL=str(tool), PANDDA_JOBS_ROOT=str(self.root),
        )

    def test_submit_poll_land_repoint(self):
        with self._settings(succeed=True):
            # Submit via the API.
            resp = self.client.post(
                reverse("job-submit"),
                {"dataset": self.dataset.id}, content_type="application/json",
            )
            self.assertEqual(resp.status_code, 201, resp.content)
            job_id = resp.json()["id"]

            # Poll until the job leaves 'running' (retrieve refreshes + lands).
            url = reverse("job-detail", args=[job_id])
            data = _wait(
                lambda: self.client.get(url).json(),
                lambda d: d["status"] != "running",
            )
            self.assertEqual(data["status"], "succeeded", data)
            self.assertIsNotNone(data["output_artifact"])

        # The refined artifact landed with correct lineage...
        refined = Artifact.objects.get(pk=data["output_artifact"])
        self.assertEqual(refined.origin, Artifact.Origin.REFINED)
        self.assertEqual(refined.relpath, f"jobs/{job_id}/refine.pdb")
        struct = self.dataset.artifacts.get(kind=Artifact.Kind.STRUCTURE,
                                            origin=Artifact.Origin.IMPORTED)
        self.assertEqual(refined.parent_id, struct.id)
        self.assertEqual(refined.produced_by_id, job_id)
        # ...and Dataset.current_model repointed to it.
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.current_model_id, refined.id)
        # The bytes are really on disk where the relpath says.
        self.assertTrue((self.root / refined.relpath).is_file())

    def test_landing_is_idempotent(self):
        with self._settings(succeed=True):
            resp = self.client.post(
                reverse("job-submit"),
                {"dataset": self.dataset.id}, content_type="application/json",
            )
            job_id = resp.json()["id"]
            url = reverse("job-detail", args=[job_id])
            _wait(lambda: self.client.get(url).json(),
                  lambda d: d["status"] != "running")
            # Poll several more times — must not create extra artifacts.
            for _ in range(3):
                self.client.get(url)
        n_refined = Artifact.objects.filter(
            origin=Artifact.Origin.REFINED, produced_by_id=job_id
        ).count()
        self.assertEqual(n_refined, 1)

    def test_failed_tool_marks_failed_no_artifact(self):
        with self._settings(succeed=False):
            resp = self.client.post(
                reverse("job-submit"),
                {"dataset": self.dataset.id}, content_type="application/json",
            )
            job_id = resp.json()["id"]
            url = reverse("job-detail", args=[job_id])
            data = _wait(lambda: self.client.get(url).json(),
                         lambda d: d["status"] != "running")
        self.assertEqual(data["status"], "failed", data)
        self.assertIsNone(data["output_artifact"])
        self.dataset.refresh_from_db()
        self.assertIsNone(self.dataset.current_model_id)

    def test_submit_gated_when_env_unavailable(self):
        # Empty prologue + a tool name that does not resolve ⇒ probe fails ⇒
        # submit is refused with a clear error (dispatch gated).
        with override_settings(
            CCP4_SETUP_SH="", CONDA_SH="", PANDDA2_CONDA_ENV="",
            REFINE_TOOL="definitely-not-a-real-tool-xyz",
        ):
            resp = self.client.post(
                reverse("job-submit"),
                {"dataset": self.dataset.id}, content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not found", resp.json()["detail"].lower())
