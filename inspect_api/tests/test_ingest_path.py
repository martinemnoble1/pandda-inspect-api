"""
Tests for the **ingest-in-place** endpoint (POST /api/v1/projects/ingest_path/).

The desktop (Electron) / CLI binding hands the server a real directory path; the
project's ``source_root`` points at that path *where it already lives* — nothing
is copied. A browser cannot reach this usefully (its picker yields no path), and
it runs ingest against an arbitrary server path, so it is **localhost-only**.
"""
import csv
from pathlib import Path

from django.test import TestCase
from rest_framework.test import APIClient

from inspect_api.models import Project

# Minimal PanDDA2 event row — only the columns the reader consumes (see
# ingest_pandda2). One dtag, one event, enough to ingest a project.
_EVENT_ROW = {
    "dtag": "FAKE-x0001",
    "event_idx": "1",
    "site_idx": "1",
    "x": "10.0",
    "y": "11.0",
    "z": "12.0",
    "z_peak": "5.0",
    "z_mean": "3.0",
    "bdc": "0.3",
    "cluster_size": "42",
    "hit_in_site_probability": "0.9",
    "interesting": "True",
    "analysed_resolution": "1.8",
    "high_resolution": "1.8",
    "low_resolution": "30.0",
    "r_free": "0.22",
    "r_work": "0.19",
    "map_uncertainty": "",
}


def _make_pandda2_tree(root: Path) -> None:
    """Write the smallest tree the PanDDA2 reader accepts."""
    analyses = root / "analyses"
    analyses.mkdir(parents=True)
    events_csv = analyses / "pandda_analyse_events.csv"
    with open(events_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_EVENT_ROW.keys()))
        writer.writeheader()
        writer.writerow(_EVENT_ROW)
    # A processed_datasets/<dtag>/ dir (may be otherwise empty — events.yaml
    # and maps are optional; absence is tolerated by the reader).
    (root / "processed_datasets" / _EVENT_ROW["dtag"]).mkdir(parents=True)


class IngestPathTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _post(self, name, path, remote="127.0.0.1"):
        return self.client.post(
            "/api/v1/projects/ingest_path/",
            {"name": name, "path": str(path)},
            format="json",
            REMOTE_ADDR=remote,
        )

    def test_ingests_in_place_without_copy(self):
        with self.settings():
            root = Path(self._tmp())
            _make_pandda2_tree(root)
            resp = self._post("inplace", root)
            self.assertEqual(resp.status_code, 201, resp.content)
            body = resp.json()
            self.assertEqual(body["flavour"], "pandda2")
            self.assertFalse(body["copied"])
            # source_root points AT the original dir — nothing duplicated.
            project = Project.objects.get(name="inplace")
            self.assertEqual(
                Path(project.source_root).resolve(), root.resolve()
            )
            self.assertEqual(body["n_datasets"], 1)

    def test_non_pandda_dir_400s(self):
        root = Path(self._tmp())  # empty dir, no markers
        resp = self._post("bad", root)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Project.objects.filter(name="bad").exists())

    def test_remote_caller_forbidden(self):
        root = Path(self._tmp())
        _make_pandda2_tree(root)
        resp = self._post("remote", root, remote="203.0.113.7")
        self.assertEqual(resp.status_code, 403)
        # Nothing ingested when refused.
        self.assertFalse(Project.objects.filter(name="remote").exists())

    def test_missing_fields_400(self):
        resp = self.client.post(
            "/api/v1/projects/ingest_path/",
            {"name": "x"},
            format="json",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 400)

    def _tmp(self) -> str:
        import tempfile

        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        return d
