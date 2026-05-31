"""Per-event autobuild ingest: events.yaml ``Build:`` block → a LIGAND_POSE
artifact + build metrics on the Event (see per-event-vs-crystal-model design
note: the pose is event-scoped provenance/overlay, never the model of record).
"""
import csv
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from inspect_api.models import Artifact, Project

PROCESSED = "processed_datasets"


def _write_tree(root: Path, dtag: str) -> None:
    """Two-event PanDDA2 tree; event 1 has a Build block, event 2 has none."""
    analyses = root / "analyses"
    analyses.mkdir(parents=True)
    proc = root / PROCESSED / dtag
    proc.mkdir(parents=True)
    with open(analyses / "pandda_analyse_events.csv", "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "dtag", "event_idx", "site_idx", "x", "y", "z", "bdc",
            "1-BDC", "analysed_resolution",
        ])
        w.writeheader()
        for idx in ("1", "2"):
            w.writerow({
                "dtag": dtag, "event_idx": idx, "site_idx": "1",
                "x": "1.0", "y": "2.0", "z": "3.0", "bdc": "0.3",
                "1-BDC": "0.7", "analysed_resolution": "1.8",
            })
    (proc / f"{dtag}-event_1_1-BDC_0.7_map.native.ccp4").touch()
    (proc / f"{dtag}-event_2_1-BDC_0.7_map.native.ccp4").touch()
    # Per-event autobuild: a chosen ligand pose for event 1. Build Path is
    # ABSOLUTE, exactly as PanDDA2 writes it.
    ab = proc / "autobuild"
    ab.mkdir()
    pose = ab / "1_1_ligand_0.pdb"
    pose.write_text(
        "HETATM    1  C1  LIG 0   1   1.0 2.0 3.0\n", encoding="utf-8"
    )
    (proc / "events.yaml").write_text(
        "1:\n"
        "  BDC: 0.3\n"
        "  Build:\n"
        f"    Build Path: {pose}\n"
        "    Build Score: 0.88\n"
        "    RSCC: 0.41\n"
        "    Optimal Contour: 2.74\n"
        "2:\n"
        "  BDC: 0.5\n",
        encoding="utf-8",
    )


class EventAutobuildIngestTests(TestCase):
    DTAG = "DTAG-x001"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _write_tree(self.root, self.DTAG)

    def _ingest(self):
        call_command(
            "ingest_pandda2", "--project", "P", "--root", str(self.root)
        )
        return Project.objects.get(name="P").datasets.get(dtag=self.DTAG)

    def test_event_with_build_gets_pose_and_metrics(self):
        e1 = self._ingest().events.get(event_num=1)
        self.assertAlmostEqual(e1.build_score, 0.88)
        self.assertAlmostEqual(e1.rscc, 0.41)
        self.assertAlmostEqual(e1.optimal_contour, 2.74)
        pose = e1.artifacts.get(kind=Artifact.Kind.LIGAND_POSE)
        # Absolute Build Path relativised to source_root.
        self.assertEqual(
            pose.relpath,
            f"{PROCESSED}/{self.DTAG}/autobuild/1_1_ligand_0.pdb",
        )
        self.assertEqual(pose.origin, Artifact.Origin.IMPORTED)
        self.assertEqual(pose.event_id, e1.id)

    def test_event_without_build_has_no_pose_or_metrics(self):
        e2 = self._ingest().events.get(event_num=2)
        self.assertIsNone(e2.rscc)
        self.assertIsNone(e2.build_score)
        self.assertIsNone(e2.optimal_contour)
        self.assertFalse(
            e2.artifacts.filter(kind=Artifact.Kind.LIGAND_POSE).exists()
        )

    def test_reingest_keeps_exactly_one_pose(self):
        # Regression: _replace_imported_dataset_artifacts used to delete the
        # pose _reconcile_events had just created (its exclude only spared
        # EVENT_MAP), so the count must stay 1 across a re-ingest.
        self._ingest()
        e1 = self._ingest().events.get(event_num=1)
        self.assertEqual(
            e1.artifacts.filter(kind=Artifact.Kind.LIGAND_POSE).count(), 1
        )

    def test_pose_is_never_current_model(self):
        # The pose is provenance/overlay, NOT the model of record. Ingest must
        # not point Event.current_model at it (would load a bare ligand).
        e1 = self._ingest().events.get(event_num=1)
        self.assertIsNone(e1.current_model)
