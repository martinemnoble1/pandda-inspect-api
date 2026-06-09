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
import hashlib
import math
from dataclasses import dataclass, field

from django.db import transaction

from .models import (
    Artifact,
    Dataset,
    Event,
    Finding,
    Project,
    Run,
    RunDataset,
    Shell,
)

# The imported artifact kinds whose relpaths constitute a dataset's "input
# bytes" — what a built/refined model derives from. A change here is what
# raises inputs_changed under a human/job artifact.
INPUT_KINDS = (Artifact.Kind.STRUCTURE, Artifact.Kind.DATA_MTZ)

# How close (A, native frame) an event's detection locus must be to a Finding's
# centroid to be the SAME binding site. Empirically two runs' detection
# centroids agree to <1A while genuine disagreements jump to >16A, so 1.5A
# separates them with wide margin (docs/MULTI_RUN_DATA_MODEL.md).
MATCH_TOLERANCE_A = 1.5


@dataclass
class ArtifactSpec:
    kind: str
    relpath: str
    # Optional embedded bytes for small dictionaries (ligand CIFs). When set,
    # stored in Artifact.contents and served from the DB rather than disk.
    contents: str = ""
    # Optional explicit map-coefficient columns for MTZ artifacts (see
    # Artifact.map_columns) — a list of {F, PHI, isDifference} dicts.
    map_columns: list = field(default_factory=list)


@dataclass
class EventSpec:
    event_num: int
    site_num: int | None = None
    # Machine metrics — updated in place on re-ingest.
    metrics: dict = field(default_factory=dict)
    # Optional event-map artifact (one imported artifact bound to this event).
    event_map_relpath: str | None = None
    # Optional autobuilt ligand-pose artifact (LIGAND_POSE; ligand-only coords
    # for this event). Provenance/overlay, not a model — see DESIGN + the
    # per-event-vs-crystal-model note. Imported origin; replaced on re-ingest.
    ligand_pose_relpath: str | None = None


@dataclass
class DatasetSpec:
    dtag: str
    subtitle: str = ""
    metrics: dict = field(default_factory=dict)
    events: list[EventSpec] = field(default_factory=list)
    # Dataset-level imported artifacts (structure, data, z-map, ligands).
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    # Relpath of the crystal START model = the apo -pandda-input.pdb
    # (ligand-free; an origin=imported STRUCTURE artifact). When present AND no
    # human/job model has superseded it, it becomes the dataset's current_model
    # — the base every event's candidate pose is merged onto. None ⇒ no apo
    # input; current_model stays unset.
    current_model_relpath: str | None = None
    # Relpath of the initial model-based-map MTZ = the dimple -pandda-input.mtz
    # (origin=imported DATA_MTZ with 2FOFCWT/FOFCWT coefficients). Becomes
    # Dataset.current_sf unless a refinement superseded it. The map analogue
    # of current_model_relpath. None ⇒ current_sf stays unset.
    current_sf_relpath: str | None = None
    # Best-available ligand-spec slot (cif|pdb|smiles|none) — recorded so the
    # UI can be honest about whether a restraint dictionary exists. Defaults to
    # "none"; the reader classifies it. See docs DESIGN §6.2.
    ligand_source: str = "none"


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
def reconcile_project(spec: ProjectSpec, run: Run = None) -> ReconcileResult:
    """Apply ``spec`` under the re-ingest policy; return a summary.

    ``run`` is the PanDDA run this ingest is the output of: its analysis
    metrics land on a RunDataset (one per run x crystal), so two runs of the
    same crystal coexist instead of clobbering each other. The lifecycle path
    passes its Run; standalone/CLI ingest passes None and we synthesise a
    deterministic Run keyed on (project, source_root) so re-ingesting the same
    tree is idempotent (same Run -> same RunDataset -> in-place upsert).
    """
    project, created = Project.objects.get_or_create(
        name=spec.name, defaults={"source_root": spec.source_root}
    )
    if not created:
        # source_root may have moved (re-ingested from a new path).
        project.source_root = spec.source_root
        project.save(update_fields=["source_root"])

    if run is None:
        run = _synthetic_run(project, spec.source_root)

    res = ReconcileResult(created=created)
    seen_dataset_ids = []

    for ds_spec in spec.datasets:
        ds, ds_inputs_before = _upsert_dataset(project, ds_spec)
        seen_dataset_ids.append(ds.id)
        res.n_datasets += 1

        # This run's metrics on the (run, crystal) row; events hang off it.
        run_dataset = _upsert_run_dataset(run, ds, ds_spec)
        _reconcile_events(run_dataset, ds_spec, res)
        # Link this run's events to the crystal's run-independent Findings
        # (sharing decisions across runs); seed new ones for unmatched sites.
        _associate_findings(run_dataset, res)
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
        # Point current_model at the apo start model, unless a human/job model
        # has superseded it (§1.3 — don't clobber post-ingest work).
        _apply_start_model(ds, ds_spec, res)

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


def _synthetic_run(project, source_root):
    """Get-or-create the placeholder Run that stands in for a standalone /
    CLI ingest (no lifecycle Run). Keyed deterministically on (project,
    source_root) so re-ingesting the same tree reuses it — the re-ingest
    idempotency the old single-row model got for free."""
    digest = hashlib.sha256(
        f"{project.id}:{source_root}".encode()
    ).hexdigest()
    run, _ = Run.objects.get_or_create(
        idempotency_key=f"ingest:{digest}",
        defaults={
            "project": project,
            "group": "local-ingest",
            "share_path": source_root,
            "out_dir": source_root,
            "status": Run.Status.SUCCEEDED,
        },
    )
    return run


def _upsert_dataset(project, ds_spec):
    """Create or update the crystal Dataset (run-INDEPENDENT identity).

    Only crystal-grain state moves here (subtitle, ligand_source) — the
    analysis metrics live on RunDataset now. Returns ``(dataset,
    input_relpaths_before)`` — the imported input relpaths *before* this
    re-ingest, used to detect input drift.
    """
    ds, _ = Dataset.objects.get_or_create(
        project=project, dtag=ds_spec.dtag
    )
    inputs_before = _imported_input_relpaths(ds)
    ds.subtitle = ds_spec.subtitle
    ds.ligand_source = ds_spec.ligand_source
    ds.save()
    return ds, inputs_before


def _upsert_run_dataset(run, dataset, ds_spec):
    """Create or update the (run, crystal) row carrying this run's metrics.
    Re-ingesting the SAME run upserts in place; a different run gets its own
    row, so the two runs' metrics never collide."""
    rd, _ = RunDataset.objects.get_or_create(run=run, dataset=dataset)
    for k, v in ds_spec.metrics.items():
        setattr(rd, k, v)
    rd.save()
    return rd


def _imported_input_relpaths(dataset) -> set[str]:
    return set(
        dataset.artifacts.filter(
            origin=Artifact.Origin.IMPORTED,
            kind__in=INPUT_KINDS,
        ).values_list("relpath", flat=True)
    )


# --- events ---------------------------------------------------------------


def _reconcile_events(run_dataset, ds_spec, res):
    """Upsert events by (run_dataset, event_num); machine metrics move.

    The key is run-scoped, so re-ingesting the SAME run upserts the same event
    rows while a DIFFERENT run's events land under its own run_dataset and never
    collide. event.dataset is set in lockstep as the run-independent crystal
    pointer. Human decision state lives on Finding now and is linked separately
    (_associate_findings), so there is nothing human to protect here."""
    dataset = run_dataset.dataset
    for ev_spec in ds_spec.events:
        event, _ = Event.objects.get_or_create(
            run_dataset=run_dataset,
            event_num=ev_spec.event_num,
            defaults={"dataset": dataset},
        )
        # Machine fields update in place; keep the crystal pointer in lockstep.
        event.dataset = dataset
        event.site_num = ev_spec.site_num
        for k, v in ev_spec.metrics.items():
            setattr(event, k, v)
        event.save()
        res.n_events += 1

        # Event-scoped imported artifacts (event map + autobuilt ligand pose):
        # replace wholesale. Built/refined event artifacts (origin != imported)
        # are untouched.
        event.artifacts.filter(
            origin=Artifact.Origin.IMPORTED,
            kind__in=(Artifact.Kind.EVENT_MAP, Artifact.Kind.LIGAND_POSE),
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
        if ev_spec.ligand_pose_relpath:
            Artifact.objects.create(
                project=dataset.project,
                dataset=dataset,
                event=event,
                kind=Artifact.Kind.LIGAND_POSE,
                relpath=ev_spec.ligand_pose_relpath,
                origin=Artifact.Origin.IMPORTED,
            )


# --- findings (run-independent decision anchors) --------------------------


def _event_locus(event):
    """The event's detection locus for matching — the run-stable
    detection_centroid, falling back to the build-snapped xyz_centroid. None
    when neither is a usable 3-vector (no coordinate to anchor on)."""
    for c in (event.detection_centroid, event.xyz_centroid):
        if isinstance(c, list) and len(c) == 3:
            return c
    return None


def _nearest_finding(dataset, locus):
    """The dataset's Finding whose centroid is closest to ``locus`` within
    MATCH_TOLERANCE_A, or None. Greedy nearest — the empirical >16A gap between
    same-site and different-site makes ties a non-issue (ADR open Q3)."""
    best, best_d = None, MATCH_TOLERANCE_A
    for f in dataset.findings.all():
        if isinstance(f.centroid, list) and len(f.centroid) == 3:
            d = math.dist(locus, f.centroid)
            if d <= best_d:
                best, best_d = f, d
    return best


def _associate_findings(run_dataset, res):
    """Link each of this run's events to the crystal's run-independent Finding
    for its binding site; seed a new (unreviewed) Finding when none is near.

    This is the cross-run sharing: a second run's event near an existing
    Finding inherits the curator's decision instead of starting blank. Findings
    are NEVER mutated here — only created/linked — so human state is structurally
    out of the import path."""
    dataset = run_dataset.dataset
    for event in run_dataset.events.all():
        locus = _event_locus(event)
        if locus is None:
            continue  # no anchor — leave unlinked (rare: no voxels, no xyz)
        finding = _nearest_finding(dataset, locus)
        if finding is None:
            finding = Finding.objects.create(
                dataset=dataset, centroid=list(locus)
            )
        elif finding.decision != Event.Decision.UNREVIEWED:
            res.n_decisions_preserved += 1
        if event.finding_id != finding.id:
            event.finding = finding
            event.save(update_fields=["finding"])


# --- artifacts ------------------------------------------------------------


def _replace_imported_dataset_artifacts(project, dataset, ds_spec) -> set:
    """Delete + recreate this dataset's imported (non-event-map) artifacts.

    Built/refined artifacts (origin != imported) are untouched. Returns the
    set of imported *input* relpaths after the replace (for drift detection).
    """
    # Event-scoped imported artifacts (event maps AND ligand poses) are owned by
    # _reconcile_events, which already replaced them this run — exclude both here
    # so we don't delete the poses it just created (they match this dataset's
    # imported, non-event-map set otherwise).
    dataset.artifacts.filter(
        origin=Artifact.Origin.IMPORTED,
    ).exclude(
        kind__in=(Artifact.Kind.EVENT_MAP, Artifact.Kind.LIGAND_POSE)
    ).delete()
    for a in ds_spec.artifacts:
        Artifact.objects.create(
            project=project,
            dataset=dataset,
            kind=a.kind,
            relpath=a.relpath,
            contents=a.contents,
            map_columns=a.map_columns,
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


def _apply_start_model(dataset, ds_spec, res):
    """Point Dataset.current_model at the apo start model (-pandda-input.pdb),
    and Dataset.current_sf at the dimple map-MTZ (-pandda-input.mtz). Both are
    the import-derived "starting" artifacts for the model and its model-based
    maps respectively — set them UNLESS post-ingest work (origin != imported)
    holds the pointer, which must not be clobbered (§1.3)."""
    _set_imported_pointer(
        dataset, "current_model", ds_spec.current_model_relpath
    )
    _set_imported_pointer(dataset, "current_sf", ds_spec.current_sf_relpath)


def _set_imported_pointer(dataset, field, relpath):
    """Set ``dataset.<field>`` to the imported artifact at ``relpath`` unless a
    non-imported (human/job) artifact currently holds it. The artifact is one
    ``_replace_imported_dataset_artifacts`` just (re)created; if ``relpath`` is
    None or the artifact is absent, leave the pointer as-is."""
    if not relpath:
        return
    # Re-read from the DB: _replace_imported_dataset_artifacts may have just
    # deleted the artifact this pointer referenced (SET_NULL nulled the FK in
    # the DB), leaving the in-memory cached FK stale.
    dataset.refresh_from_db(fields=[field])
    current = getattr(dataset, field)
    if current is not None and current.origin != Artifact.Origin.IMPORTED:
        return  # human/job artifact wins — leave it
    art = dataset.artifacts.filter(
        relpath=relpath, origin=Artifact.Origin.IMPORTED
    ).first()
    if art is not None and getattr(dataset, f"{field}_id") != art.id:
        setattr(dataset, field, art)
        dataset.save(update_fields=[field])
