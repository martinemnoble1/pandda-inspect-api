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
    # Event 1 carries a Position Array (the detection voxels) whose mean is
    # [11, 21, 31] — deliberately NOWHERE NEAR the CSV x,y,z (1,2,3), the
    # build-snapped centroid. Event 2 has no voxels (detection_centroid []).
    (proc / "events.yaml").write_text(
        "1:\n"
        "  BDC: 0.3\n"
        "  Position Array:\n"
        "  - [10.0, 20.0, 30.0]\n"
        "  - [12.0, 22.0, 32.0]\n"
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

    def test_detection_centroid_is_voxel_mean_not_build_snapped(self):
        # The detection locus is the mean of the Position Array voxels
        # ([11,21,31]) — the run-stable match key — NOT the build-snapped
        # xyz_centroid ([1,2,3] from the CSV). The two MUST differ.
        e1 = self._ingest().events.get(event_num=1)
        self.assertEqual(e1.detection_centroid, [11.0, 21.0, 31.0])
        self.assertEqual(e1.xyz_centroid, [1.0, 2.0, 3.0])
        self.assertNotEqual(e1.detection_centroid, e1.xyz_centroid)

    def test_detection_centroid_empty_without_voxels(self):
        # Event 2 has no Position Array (and no Build) — detection_centroid is
        # [], but xyz_centroid still comes from the CSV row.
        e2 = self._ingest().events.get(event_num=2)
        self.assertEqual(e2.detection_centroid, [])
        self.assertEqual(e2.xyz_centroid, [1.0, 2.0, 3.0])

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
        # not point Event.current_model at it (would load a bare ligand) — and
        # pose_merged starts unset (apo start-model: nothing pre-merged).
        e1 = self._ingest().events.get(event_num=1)
        pose = e1.artifacts.get(kind=Artifact.Kind.LIGAND_POSE)
        self.assertNotEqual(e1.dataset.current_model_id, pose.id)
        self.assertIsNone(e1.pose_merged)


class LandBuiltModelTests(TestCase):
    """buildservice.land_built_model: persist a client-merged model as an
    origin=built Artifact, version it, repoint current_model, flag event."""

    def setUp(self):
        from inspect_api.models import Dataset, Event
        self.root = Path(tempfile.mkdtemp())
        self.project = Project.objects.create(
            name="BP", source_root=str(self.root)
        )
        self.dataset = Dataset.objects.create(project=self.project, dtag="d1")
        # The apo input (start model). buildservice's parent fallback matches
        # it by the -pandda-input.pdb suffix, so the fixture uses that name.
        self.struct = Artifact.objects.create(
            dataset=self.dataset, kind=Artifact.Kind.STRUCTURE,
            relpath="d1-pandda-input.pdb", origin=Artifact.Origin.IMPORTED,
        )
        self.event = Event.objects.create(dataset=self.dataset, event_num=1)

    def test_lands_built_model_and_repoints(self):
        from inspect_api.buildservice import land_built_model
        pdb = "ATOM      1  CA  ALA A   1      0.0  0.0  0.0  1.0  0.0\n"
        # merge=True path: a ligand merge for this event sets pose_merged.
        built = land_built_model(self.event, pdb, pose_merged=True)
        self.assertEqual(built.origin, Artifact.Origin.BUILT)
        self.assertEqual(built.parent_id, self.struct.id)
        self.assertEqual(built.relpath, "builds/1/model.pdb")
        self.assertTrue((self.root / built.relpath).is_file())
        self.dataset.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.dataset.current_model_id, built.id)
        self.assertTrue(self.event.pose_merged)

    def test_generic_commit_does_not_set_pose_merged(self):
        # A generic save (default pose_merged=False) lands the model + repoints
        # current_model, but must NOT flag the event's pose merged (deleting
        # waters / fixing a rotamer is not a ligand merge).
        from inspect_api.buildservice import land_built_model
        pdb = "ATOM      1  CA  ALA A   1      0.0  0.0  0.0  1.0  0.0\n"
        built = land_built_model(self.event, pdb)
        self.dataset.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.dataset.current_model_id, built.id)
        self.assertIsNone(self.event.pose_merged)

    def test_versions_write_once(self):
        from inspect_api.buildservice import land_built_model
        pdb = "HETATM    1  C1  LIG A   1      0.0  0.0  0.0  1.0  0.0\n"
        b1 = land_built_model(self.event, pdb)
        b2 = land_built_model(self.event, pdb)
        # New version, parent = the previous current_model (b1), no clobber.
        self.assertEqual(b1.relpath, "builds/1/model.pdb")
        self.assertEqual(b2.relpath, "builds/2/model.pdb")
        self.assertEqual(b2.parent_id, b1.id)
        self.assertTrue((self.root / b1.relpath).is_file())

    def test_empty_model_rejected(self):
        from inspect_api.buildservice import BuildError, land_built_model
        with self.assertRaises(BuildError):
            land_built_model(self.event, "REMARK no atoms here\n")
