"""
Re-ingest reconciliation — the additive, import-scoped policy.

Both ingest readers (``ingest_pandda`` for PanDDA1, ``ingest_pandda2`` for
PanDDA2) parse their very different on-disk formats into the *same* normalized
specs below, then hand them here. This module is the single place that decides
what a re-ingest does to existing rows — the "real design core" of roadmap #2
(see docs/DESIGN-artifacts-and-jobs.md §1.3).

Policy ("surface, don't resolve"):

* **Imported artifacts** (``origin=imported``) are replaced wholesale — they
  are a frozen projection of the filesystem, so a rerun rewrites them.
* **Machine metrics** (resolution, R-factors, score, interesting, event
  geometry) update in place — the analysis's own opinion is allowed to move.
* **Human decision state** (decision, confidence, comment, inspected_by/at) is
  NEVER touched — it is the curator's assertion, not the filesystem's.
* **Built / refined artifacts** (``origin != imported``) are NEVER touched —
  they are the scientific work product a clobbering re-ingest would lose.
* **current_model pointers**: if a pointer references an imported artifact it
  is repointed to the new import; if it references a built/refined artifact it
  LEFT in place, and ``inputs_changed`` is raised when the underlying import
  inputs changed — "the analysis under this built model changed; a human
  should look." We flag, we do not auto-merge (that is a scientific judgement).

A first ingest of a never-seen project is just the degenerate case: nothing to
preserve, everything created.
"""
from dataclasses import dataclass, field

from django.db import transaction

from .models import Artifact, Dataset, Event, Project, Shell

# The imported artifact kinds whose relpaths constitute a dataset's "input
# bytes" — what a built/refined model derives from. A change here is what
# raises inputs_changed under a human/job artifact.
INPUT_KINDS = (Artifact.Kind.STRUCTURE, Artifact.Kind.DATA_MTZ)


@dataclass
class ArtifactSpec:
    kind: str
    relpath: str
    # Optional embedded bytes for small dictionaries (ligand CIFs). When set,
    # stored in Artifact.contents and served from the DB rather than disk.
    contents: str = ""


@dataclass
class EventSpec:
    event_num: int
    site_num: int | None = None
    # Machine metrics — updated in place on re-ingest.
    metrics: dict = field(default_factory=dict)
    # Optional event-map artifact (one imported artifact bound to this event).
    event_map_relpath: str | None = None


@dataclass
class DatasetSpec:
    dtag: str
    subtitle: str = ""
    metrics: dict = field(default_factory=dict)
    events: list[EventSpec] = field(default_factory=list)
    # Dataset-level imported artifacts (structure, data, z-map, ligands).
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    # Relpath of the analysis's own merged model (PanDDA2 autobuild
    # pandda-model.pdb): a STRUCTURE artifact carrying the built ligand, but
    # origin=imported (re-derivable analysis output, refreshed on re-ingest;
    # "built"/"refined" are reserved for post-ingest human/job work). When
    # present AND no human/job model has superseded it, it becomes the
    # dataset's current_model so the viewer shows the built ligand. None ⇒ no
    # analysis model (33/201 in BAZ2B); current_model stays unset.
    current_model_relpath: str | None = None


@dataclass
class ProjectSpec:
    name: str
    source_root: str
    datasets: list[DatasetSpec] = field(default_factory=list)
    # Project-level imported artifacts (e.g. report HTML).
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    shells: list[dict] = field(default_factory=list)


@dataclass
class ReconcileResult:
    created: bool  # True if the project did not previously exist
    n_datasets: int = 0
    n_events: int = 0
    n_imported_artifacts: int = 0
    n_shells: int = 0
    # Preserved / flagged counts — the evidence the policy ran.
    n_decisions_preserved: int = 0
    n_built_preserved: int = 0
    n_inputs_changed: int = 0


@transaction.atomic
def reconcile_project(spec: ProjectSpec) -> ReconcileResult:
    """Apply ``spec`` under the re-ingest policy; return a summary."""
    project, created = Project.objects.get_or_create(
        name=spec.name, defaults={"source_root": spec.source_root}
    )
    if not created:
        # source_root may have moved (re-ingested from a new path).
        project.source_root = spec.source_root
        project.save(update_fields=["source_root"])

    res = ReconcileResult(created=created)
    seen_dataset_ids = []

    for ds_spec in spec.datasets:
        ds, ds_inputs_before = _upsert_dataset(project, ds_spec)
        seen_dataset_ids.append(ds.id)
        res.n_datasets += 1

        _reconcile_events(ds, ds_spec, res)
        ds_inputs_after = _replace_imported_dataset_artifacts(
            project, ds, ds_spec
        )
        res.n_imported_artifacts += len(ds_spec.artifacts) + sum(
            1 for e in ds_spec.events if e.event_map_relpath
        )

        # Pointer / flag policy for the dataset's current_model.
        _apply_pointer_policy(
            ds, ds_inputs_before, ds_inputs_after, res
        )
        # Point current_model at the analysis's own merged model (autobuild),
        # unless a human/job model has superseded it (§1.3 — don't clobber
        # post-ingest work).
        _apply_analysis_model(ds, ds_spec, res)

    # Project-level imported artifacts (reports): replace wholesale.
    _replace_imported_project_artifacts(project, spec)
    res.n_imported_artifacts += len(spec.artifacts)

    # Shells are pure analysis provenance — replace wholesale.
    project.shells.all().delete()
    for sh in spec.shells:
        Shell.objects.create(project=project, **sh)
    res.n_shells = len(spec.shells)

    # Datasets that vanished from the input are NOT deleted: they may carry
    # human decisions or built models. Leaving them is the conservative,
    # surface-don't-resolve choice (a stale dataset is visible, not lost).

    return res


# --- datasets -------------------------------------------------------------


def _upsert_dataset(project, ds_spec):
    """Create or update a Dataset; metrics move, identity is (project, dtag).

    Returns ``(dataset, input_relpaths_before)`` — the set of imported input
    relpaths *before* this re-ingest, used to detect input drift.
    """
    ds, _ = Dataset.objects.get_or_create(
        project=project, dtag=ds_spec.dtag
    )
    inputs_before = _imported_input_relpaths(ds)
    # Machine metrics + subtitle move in place; human state lives on Events,
    # not here, so there is nothing on Dataset to protect.
    ds.subtitle = ds_spec.subtitle
    for k, v in ds_spec.metrics.items():
        setattr(ds, k, v)
    ds.save()
    return ds, inputs_before


def _imported_input_relpaths(dataset) -> set[str]:
    return set(
        dataset.artifacts.filter(
            origin=Artifact.Origin.IMPORTED,
            kind__in=INPUT_KINDS,
        ).values_list("relpath", flat=True)
    )


# --- events ---------------------------------------------------------------


def _reconcile_events(dataset, ds_spec, res):
    """Upsert events by (dataset, event_num); metrics move, decisions stay."""
    for ev_spec in ds_spec.events:
        event, created = Event.objects.get_or_create(
            dataset=dataset, event_num=ev_spec.event_num
        )
        # Machine fields update in place.
        event.site_num = ev_spec.site_num
        for k, v in ev_spec.metrics.items():
            setattr(event, k, v)
        # Human decision state (decision/confidence/comment/inspected_by/at)
        # is deliberately NOT assigned here — it survives the re-ingest.
        if not created and event.decision != Event.Decision.UNREVIEWED:
            res.n_decisions_preserved += 1
        event.save()
        res.n_events += 1

        # Event-map artifact (imported): replace this event's imported maps.
        event.artifacts.filter(
            origin=Artifact.Origin.IMPORTED,
            kind=Artifact.Kind.EVENT_MAP,
        ).delete()
        if ev_spec.event_map_relpath:
            Artifact.objects.create(
                project=dataset.project,
                dataset=dataset,
                event=event,
                kind=Artifact.Kind.EVENT_MAP,
                relpath=ev_spec.event_map_relpath,
                origin=Artifact.Origin.IMPORTED,
            )


# --- artifacts ------------------------------------------------------------


def _replace_imported_dataset_artifacts(project, dataset, ds_spec) -> set:
    """Delete + recreate this dataset's imported (non-event-map) artifacts.

    Built/refined artifacts (origin != imported) are untouched. Returns the
    set of imported *input* relpaths after the replace (for drift detection).
    """
    dataset.artifacts.filter(
        origin=Artifact.Origin.IMPORTED,
    ).exclude(kind=Artifact.Kind.EVENT_MAP).delete()
    for a in ds_spec.artifacts:
        Artifact.objects.create(
            project=project,
            dataset=dataset,
            kind=a.kind,
            relpath=a.relpath,
            contents=a.contents,
            origin=Artifact.Origin.IMPORTED,
        )
    return _imported_input_relpaths(dataset)


def _replace_imported_project_artifacts(project, spec):
    """Replace project-level imported artifacts (e.g. report HTML)."""
    project.artifacts.filter(
        origin=Artifact.Origin.IMPORTED, dataset__isnull=True
    ).delete()
    for a in spec.artifacts:
        Artifact.objects.create(
            project=project,
            kind=a.kind,
            relpath=a.relpath,
            origin=Artifact.Origin.IMPORTED,
        )


# --- pointer / flag policy ------------------------------------------------


def _apply_pointer_policy(dataset, inputs_before, inputs_after, res):
    """Repoint or flag current_model per the surface-don't-resolve policy.

    Applies to Dataset.current_model and each Event.current_model:
    * pointer at an imported artifact -> the import was just replaced, so the
      old target is gone; clear/repoint is handled by the SET_NULL on delete,
      and there is nothing human to protect.
    * pointer at a built/refined artifact -> LEAVE it; raise inputs_changed if
      the imported inputs drifted.
    """
    inputs_drifted = inputs_before != inputs_after and bool(inputs_before)

    # Re-read pointer state from the DB: _replace_imported_dataset_artifacts
    # may have just deleted the imported artifact this pointer referenced (the
    # SET_NULL nulls current_model_id), leaving the in-memory FK stale.
    dataset.refresh_from_db(fields=["current_model"])

    # Dataset-level pointer (refined whole-crystal model).
    cm = dataset.current_model
    if cm is not None and cm.origin != Artifact.Origin.IMPORTED:
        if inputs_drifted and not dataset.inputs_changed:
            dataset.inputs_changed = True
            dataset.save(update_fields=["inputs_changed"])
            res.n_inputs_changed += 1
        res.n_built_preserved += 1

    # Event-level pointers (built ligand models).
    for event in dataset.events.exclude(current_model__isnull=True):
        ecm = event.current_model
        if ecm is not None and ecm.origin != Artifact.Origin.IMPORTED:
            if inputs_drifted and not event.inputs_changed:
                event.inputs_changed = True
                event.save(update_fields=["inputs_changed"])
                res.n_inputs_changed += 1
            res.n_built_preserved += 1


def _apply_analysis_model(dataset, ds_spec, res):
    """Point Dataset.current_model at the analysis's merged model (autobuild).

    The model is an ``origin=imported`` STRUCTURE artifact already (re)created
    by ``_replace_imported_dataset_artifacts`` from ``ds_spec.artifacts``. We
    set it as current_model UNLESS a post-ingest human/job model
    (``origin != imported``) currently holds the pointer — that work must not
    be clobbered (§1.3). If the dataset has no analysis model, the pointer is
    left as-is (a prior import model was just deleted, so SET_NULL cleared it).
    """
    relpath = ds_spec.current_model_relpath
    if not relpath:
        return
    cm = dataset.current_model
    if cm is not None and cm.origin != Artifact.Origin.IMPORTED:
        return  # human/job model wins — leave it
    model = dataset.artifacts.filter(
        relpath=relpath, origin=Artifact.Origin.IMPORTED
    ).first()
    if model is not None and dataset.current_model_id != model.id:
        dataset.current_model = model
        dataset.save(update_fields=["current_model"])
