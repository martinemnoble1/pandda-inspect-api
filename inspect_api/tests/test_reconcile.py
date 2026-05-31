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

from inspect_api.models import Artifact, Dataset, Event, Project
from inspect_api.reconcile import (
    ArtifactSpec,
    DatasetSpec,
    EventSpec,
    ProjectSpec,
    reconcile_project,
)

NAME = "Proj"
ROOT = "/data/proj"


def _spec(struct_relpath="ds1-input.pdb", z_peak=5.0, score=0.8,
          model_relpath=None):
    """One-dataset, one-event spec; params let a re-ingest vary inputs.

    ``model_relpath`` simulates a PanDDA2 autobuild merged model: it is added
    as an imported STRUCTURE artifact and named as current_model_relpath.
    """
    artifacts = [
        ArtifactSpec(Artifact.Kind.STRUCTURE, struct_relpath),
        ArtifactSpec(Artifact.Kind.DATA_MTZ, "ds1-input.mtz"),
    ]
    if model_relpath:
        artifacts.append(ArtifactSpec(Artifact.Kind.STRUCTURE, model_relpath))
    return ProjectSpec(
        name=NAME,
        source_root=ROOT,
        datasets=[
            DatasetSpec(
                dtag="ds1",
                metrics={"r_free": 0.21},
                events=[
                    EventSpec(
                        event_num=1,
                        site_num=1,
                        metrics={"z_peak": z_peak, "score": score},
                        event_map_relpath="ds1-event_1_map.ccp4",
                    )
                ],
                artifacts=artifacts,
                current_model_relpath=model_relpath,
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


MODEL = "ds1-model.pdb"


class AnalysisModelPointerTests(TestCase):
    """PanDDA2 autobuild merged model -> Dataset.current_model.

    origin=imported (re-derivable); must not clobber a human/job model.
    """

    def test_analysis_model_becomes_current_model(self):
        reconcile_project(_spec(model_relpath=MODEL))
        ds = Dataset.objects.get(dtag="ds1")
        self.assertIsNotNone(ds.current_model_id)
        self.assertEqual(ds.current_model.relpath, MODEL)
        self.assertEqual(ds.current_model.origin, Artifact.Origin.IMPORTED)

    def test_no_model_leaves_pointer_unset(self):
        reconcile_project(_spec(model_relpath=None))
        ds = Dataset.objects.get(dtag="ds1")
        self.assertIsNone(ds.current_model_id)

    def test_reingest_refreshes_analysis_model(self):
        # First ingest sets it; re-ingest re-creates the imported artifact and
        # re-points (the old imported row is replaced, not preserved).
        reconcile_project(_spec(model_relpath=MODEL))
        reconcile_project(_spec(model_relpath=MODEL))
        ds = Dataset.objects.get(dtag="ds1")
        # Exactly one imported model at that relpath (no accumulation).
        self.assertEqual(
            ds.artifacts.filter(
                relpath=MODEL, origin=Artifact.Origin.IMPORTED
            ).count(),
            1,
        )
        self.assertEqual(ds.current_model.relpath, MODEL)

    def test_human_model_not_clobbered_by_analysis_model(self):
        # A human/job model on the pointer must win over the analysis model.
        reconcile_project(_spec(model_relpath=MODEL))
        ds = Dataset.objects.get(dtag="ds1")
        built = Artifact.objects.create(
            dataset=ds, kind=Artifact.Kind.STRUCTURE,
            relpath="ds1-human-build.pdb", origin=Artifact.Origin.BUILT,
        )
        ds.current_model = built
        ds.save()
        # Re-ingest: the analysis model is refreshed, but the pointer stays on
        # the human build (don't clobber post-ingest work).
        reconcile_project(_spec(model_relpath=MODEL))
        ds.refresh_from_db()
        self.assertEqual(ds.current_model_id, built.id)
