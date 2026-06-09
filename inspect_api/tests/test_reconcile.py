"""
Tests for the re-ingest reconciliation policy (docs/DESIGN §1.3).

These drive ``reconcile_project`` with hand-built ProjectSpecs (no filesystem),
so they isolate the *policy* from either reader's parsing. The contract under
test: a re-ingest is additive + import-scoped — it refreshes imported files
and machine metrics, NEVER touches human decisions or built/refined models, and
flags (does not resolve) input drift under a human/job artifact.
"""
from django.test import TestCase
from django.utils import timezone

from inspect_api.models import (
    Artifact,
    Dataset,
    Event,
    Project,
    Run,
    RunDataset,
)
from inspect_api.reconcile import (
    ArtifactSpec,
    DatasetSpec,
    EventSpec,
    ProjectSpec,
    reconcile_project,
)

NAME = "Proj"
ROOT = "/data/proj"


def _spec(struct_relpath="ds1-input.pdb", z_peak=5.0, score=0.8, r_free=0.21):
    """One-dataset, one-event spec; params let a re-ingest vary inputs.

    ``struct_relpath`` is the apo input STRUCTURE, which is also the dataset's
    current_model start (the apo start-model pivot).
    """
    return ProjectSpec(
        name=NAME,
        source_root=ROOT,
        datasets=[
            DatasetSpec(
                dtag="ds1",
                metrics={"r_free": r_free},
                events=[
                    EventSpec(
                        event_num=1,
                        site_num=1,
                        metrics={"z_peak": z_peak, "score": score},
                        event_map_relpath="ds1-event_1_map.ccp4",
                    )
                ],
                artifacts=[
                    ArtifactSpec(Artifact.Kind.STRUCTURE, struct_relpath),
                    ArtifactSpec(Artifact.Kind.DATA_MTZ, "ds1-input.mtz"),
                ],
                current_model_relpath=struct_relpath,
                current_sf_relpath="ds1-input.mtz",
            )
        ],
    )


class FirstIngestTests(TestCase):
    def test_creates_everything_as_imported(self):
        res = reconcile_project(_spec())
        self.assertTrue(res.created)
        self.assertEqual(res.n_datasets, 1)
        self.assertEqual(res.n_events, 1)
        project = Project.objects.get(name=NAME)
        # structure + mtz + event map = 3 imported artifacts.
        self.assertEqual(project.artifacts.count(), 3)
        self.assertTrue(
            all(
                a.origin == Artifact.Origin.IMPORTED
                for a in project.artifacts.all()
            )
        )


class ReIngestPreservesHumanStateTests(TestCase):
    def setUp(self):
        reconcile_project(_spec())
        self.event = Event.objects.get(dataset__dtag="ds1", event_num=1)

    def test_decision_survives_reingest(self):
        self.event.decision = Event.Decision.HIT
        self.event.comment = "clear density"
        self.event.inspected_by = "mn"
        self.event.inspected_at = timezone.now()
        self.event.save()

        # Re-ingest with *changed machine metrics*.
        res = reconcile_project(_spec(z_peak=9.9, score=0.95))

        self.assertFalse(res.created)
        self.assertEqual(res.n_decisions_preserved, 1)
        self.event.refresh_from_db()
        # Human state untouched...
        self.assertEqual(self.event.decision, Event.Decision.HIT)
        self.assertEqual(self.event.comment, "clear density")
        self.assertEqual(self.event.inspected_by, "mn")
        # ...machine metrics refreshed.
        self.assertEqual(self.event.z_peak, 9.9)
        self.assertEqual(self.event.score, 0.95)

    def test_no_duplicate_rows_on_reingest(self):
        reconcile_project(_spec())
        self.assertEqual(Event.objects.filter(event_num=1).count(), 1)
        self.assertEqual(Dataset.objects.filter(dtag="ds1").count(), 1)
        # Imported artifacts replaced, not accumulated: still 3.
        self.assertEqual(
            Artifact.objects.filter(origin=Artifact.Origin.IMPORTED).count(),
            3,
        )


class ReIngestPreservesBuiltModelTests(TestCase):
    def setUp(self):
        reconcile_project(_spec())
        self.event = Event.objects.get(dataset__dtag="ds1", event_num=1)
        self.dataset = self.event.dataset
        struct = self.dataset.artifacts.get(kind=Artifact.Kind.STRUCTURE)
        # A human builds a ligand: write-once artifact + pointer.
        self.built = Artifact.objects.create(
            dataset=self.dataset,
            event=self.event,
            kind=Artifact.Kind.STRUCTURE,
            relpath="ds1-built.pdb",
            origin=Artifact.Origin.BUILT,
            parent=struct,
        )
        self.event.current_model = self.built
        self.event.save()

    def test_built_model_and_pointer_survive_unchanged_inputs(self):
        # Re-ingest with the SAME structure relpath -> no input drift.
        res = reconcile_project(_spec())
        self.assertEqual(res.n_built_preserved, 1)
        self.assertEqual(res.n_inputs_changed, 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.current_model_id, self.built.id)
        self.assertFalse(self.event.inputs_changed)
        # The built artifact still exists, untouched.
        self.assertTrue(Artifact.objects.filter(id=self.built.id).exists())

    def test_input_drift_flags_but_does_not_repoint(self):
        # Re-ingest with a DIFFERENT structure relpath -> input drift.
        res = reconcile_project(_spec(struct_relpath="ds1-input-v2.pdb"))
        self.assertEqual(res.n_inputs_changed, 1)
        self.event.refresh_from_db()
        # Flagged for human attention...
        self.assertTrue(self.event.inputs_changed)
        # ...but the pointer is LEFT on the human model (don't resolve).
        self.assertEqual(self.event.current_model_id, self.built.id)
        self.assertTrue(Artifact.objects.filter(id=self.built.id).exists())


APO = "ds1-input.pdb"  # _spec's default struct_relpath = the apo start model


class MultiRunCoexistenceTests(TestCase):
    """Two runs of the SAME crystal coexist (Phase B): distinct RunDatasets +
    Event rows, metrics never clobber, the crystal anchor is shared, and a
    re-ingest of one run stays idempotent."""

    def setUp(self):
        self.project = Project.objects.create(name=NAME, source_root=ROOT)
        self.run_a = Run.objects.create(
            project=self.project, group="A", share_path=ROOT,
            idempotency_key="k-a", status=Run.Status.SUCCEEDED,
        )
        self.run_b = Run.objects.create(
            project=self.project, group="B", share_path=ROOT,
            idempotency_key="k-b", status=Run.Status.SUCCEEDED,
        )
        reconcile_project(_spec(z_peak=5.0, r_free=0.21), run=self.run_a)
        reconcile_project(_spec(z_peak=9.0, r_free=0.30), run=self.run_b)

    def test_one_crystal_two_run_datasets_two_events(self):
        self.assertEqual(Dataset.objects.filter(dtag="ds1").count(), 1)
        ds = Dataset.objects.get(dtag="ds1")
        self.assertEqual(ds.run_datasets.count(), 2)
        # event_num=1 exists once PER run — no collision on the old key.
        self.assertEqual(ds.events.filter(event_num=1).count(), 2)

    def test_metrics_do_not_clobber(self):
        ds = Dataset.objects.get(dtag="ds1")
        rd_a = ds.run_datasets.get(run=self.run_a)
        rd_b = ds.run_datasets.get(run=self.run_b)
        self.assertAlmostEqual(rd_a.r_free, 0.21)
        self.assertAlmostEqual(rd_b.r_free, 0.30)
        # Each run's event keeps its own machine metric.
        self.assertEqual(
            rd_a.events.get(event_num=1).z_peak, 5.0
        )
        self.assertEqual(
            rd_b.events.get(event_num=1).z_peak, 9.0
        )

    def test_primary_run_dataset_is_latest_run(self):
        ds = Dataset.objects.get(dtag="ds1")
        self.assertEqual(ds.primary_run_dataset.run_id, self.run_b.id)

    def test_reingest_same_run_is_idempotent(self):
        reconcile_project(_spec(z_peak=7.0, r_free=0.25), run=self.run_a)
        ds = Dataset.objects.get(dtag="ds1")
        # Still two run_datasets / two events — run A upserted in place.
        self.assertEqual(ds.run_datasets.count(), 2)
        self.assertEqual(ds.events.filter(event_num=1).count(), 2)
        self.assertEqual(
            RunDataset.objects.get(run=self.run_a, dataset=ds).r_free, 0.25
        )


class StartModelPointerTests(TestCase):
    """The apo input (-pandda-input.pdb) becomes Dataset.current_model at
    ingest; the pointer policy must not clobber a human/job model."""

    def test_apo_becomes_current_model(self):
        reconcile_project(_spec())
        ds = Dataset.objects.get(dtag="ds1")
        self.assertIsNotNone(ds.current_model_id)
        self.assertEqual(ds.current_model.relpath, APO)
        self.assertEqual(ds.current_model.origin, Artifact.Origin.IMPORTED)

    def test_dimple_mtz_becomes_current_sf(self):
        # The model-based-map MTZ slot starts at the dimple input MTZ.
        reconcile_project(_spec())
        ds = Dataset.objects.get(dtag="ds1")
        self.assertIsNotNone(ds.current_sf_id)
        self.assertEqual(ds.current_sf.relpath, "ds1-input.mtz")
        self.assertEqual(ds.current_sf.origin, Artifact.Origin.IMPORTED)

    def test_human_model_not_clobbered_by_apo(self):
        # A human/job built model on the pointer must survive re-ingest (the
        # apo start-model must not overwrite post-ingest work).
        reconcile_project(_spec())
        ds = Dataset.objects.get(dtag="ds1")
        built = Artifact.objects.create(
            dataset=ds, kind=Artifact.Kind.STRUCTURE,
            relpath="builds/1/model.pdb", origin=Artifact.Origin.BUILT,
        )
        ds.current_model = built
        ds.save()
        reconcile_project(_spec())
        ds.refresh_from_db()
        self.assertEqual(ds.current_model_id, built.id)
