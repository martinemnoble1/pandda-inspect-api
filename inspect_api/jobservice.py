"""
Job orchestration — the bridge between the DB (Job/Artifact/Dataset) and the
runner seam (jobs.py). Kept out of the view so the submit/poll/land logic is
unit-testable directly.

Two operations:

* :func:`submit_refinement` — resolve a dataset's current-best inputs to
  paths, build a JobSpec, create the Job row, hand it to the runner.
* :func:`refresh_job` — poll the runner's status file and, on the FIRST
  observed success, idempotently land the output as an Artifact(origin=refined)
  and repoint Dataset.current_model (DESIGN §5.4).

The input-selection + lineage rules are DESIGN §5.3; the landing idempotency
is §5.4 (select_for_update + null-output guard so a second poll is a no-op).
"""
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .jobs import JobSpec, get_runner
from .models import Artifact, Dataset, Job


class JobError(Exception):
    """A refinement could not be submitted (bad inputs, env not available)."""


def _resolve_path(artifact: Artifact) -> Path:
    """Absolute on-disk path for an artifact, via its project's source_root.

    Mirrors the download view's resolution: relpath under source_root (the tree
    ingested from), falling back to PANDDA_DATA_ROOT/<name>.
    """
    project = artifact.owning_project
    root = Path(
        project.source_root
        or (Path(settings.PANDDA_DATA_ROOT) / project.name)
    )
    return (root / artifact.relpath).resolve()


def _job_root(dataset: Dataset) -> Path:
    """Base dir under which this dataset's jobs write.

    The project's ``source_root`` when set (so outputs sit beside the data they
    refine and the relpath resolves like any other artifact), else the
    configured ``PANDDA_JOBS_ROOT``.
    """
    root = dataset.project.source_root
    return Path(root) if root else Path(settings.PANDDA_JOBS_ROOT)


def _input_pdb(dataset: Dataset) -> Artifact | None:
    """The PDB to refine: the dataset's current best model if set, else the
    imported input structure (DESIGN §5.3). The chosen artifact becomes the
    refined output's lineage ``parent``."""
    if dataset.current_model_id:
        return dataset.current_model
    return dataset.artifacts.filter(
        kind=Artifact.Kind.STRUCTURE
    ).order_by("id").first()


def submit_refinement(dataset: Dataset, params: dict | None = None) -> Job:
    """Create + dispatch a giant.quick_refine Job for ``dataset``.

    Resolves inputs to paths (the JobSpec carries paths; the API request does
    not), gates on the runner probe, and records a queued/running Job.
    """
    runner = get_runner()
    probe = runner.probe()
    if not probe.get("available"):
        raise JobError(probe.get("reason") or "refinement tool unavailable")

    pdb = _input_pdb(dataset)
    mtz = dataset.artifacts.filter(
        kind=Artifact.Kind.DATA_MTZ
    ).order_by("id").first()
    if pdb is None or mtz is None:
        raise JobError(
            "dataset lacks an input structure and/or MTZ to refine"
        )
    cif = dataset.artifacts.filter(
        kind=Artifact.Kind.LIGAND
    ).order_by("id").first()

    spec = JobSpec(
        tool=settings.REFINE_TOOL,
        inputs={
            "pdb": str(_resolve_path(pdb)),
            "mtz": str(_resolve_path(mtz)),
            "cif": str(_resolve_path(cif)) if cif else "",
        },
        params=params or {},
    )

    # Create the Job first so we have an id for the workdir, then submit.
    job = Job.objects.create(
        tool=spec.tool,
        dataset=dataset,
        spec={"inputs": spec.inputs, "params": spec.params},
        status=Job.Status.QUEUED,
    )
    # Workdir under the project's OWN root so the refined artifact's relpath
    # (jobs/<id>/refine.pdb) resolves through the same source_root logic as
    # every other artifact — including for in-place-ingested projects whose
    # root is outside PANDDA_JOBS_ROOT (DESIGN §5.2).
    workdir = _job_root(dataset) / f"jobs/{job.id}"
    # Record the input artifact id so landing can set lineage parent.
    job.spec["input_pdb_artifact_id"] = pdb.id
    handle = runner.submit(spec, workdir)
    job.runner_handle = handle
    job.status = Job.Status.RUNNING
    job.save(update_fields=["runner_handle", "status", "spec"])
    return job


def refresh_job(job: Job) -> Job:
    """Poll the runner; land the output on first observed success (idempotent).

    Safe to call repeatedly — once ``output_artifact`` is set it is a no-op.
    """
    if job.status in (Job.Status.SUCCEEDED, Job.Status.FAILED):
        return job
    if not job.runner_handle:
        return job

    st = get_runner().status(job.runner_handle)
    state = st.get("state")
    if state == "running":
        return job
    if state == "failed":
        job.status = Job.Status.FAILED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        return job
    if state == "succeeded":
        return _land(job, st)
    return job


@transaction.atomic
def _land(job: Job, status: dict) -> Job:
    """Register the refined Artifact + repoint Dataset.current_model, once.

    Locked + null-output-guarded so concurrent polls can't double-create
    (DESIGN §5.4).
    """
    job = Job.objects.select_for_update().get(pk=job.pk)
    if job.output_artifact_id is not None:
        return job  # already landed by a prior poll — no-op

    out_pdb = (status.get("outputs") or {}).get("pdb")
    if not out_pdb:
        # Succeeded but produced no model we recognise — mark succeeded with
        # no artifact rather than inventing one.
        job.status = Job.Status.SUCCEEDED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        return job

    dataset = job.dataset
    parent_id = (job.spec or {}).get("input_pdb_artifact_id")
    parent = (
        Artifact.objects.filter(pk=parent_id).first() if parent_id else None
    )
    # Workdir lives under PANDDA_JOBS_ROOT; the refined artifact's relpath is
    # expressed relative to the project source_root so the download view
    # resolves it like any other artifact (DESIGN §5.2).
    relpath = _refined_relpath(dataset, job, out_pdb)
    refined = Artifact.objects.create(
        project=dataset.project,
        dataset=dataset,
        kind=Artifact.Kind.STRUCTURE,
        relpath=relpath,
        origin=Artifact.Origin.REFINED,
        parent=parent,
        produced_by=job,
    )
    dataset.current_model = refined
    dataset.save(update_fields=["current_model"])

    job.output_artifact = refined
    job.status = Job.Status.SUCCEEDED
    job.finished_at = timezone.now()
    job.save(update_fields=["output_artifact", "status", "finished_at"])
    return job


def _refined_relpath(dataset: Dataset, job: Job, out_name: str) -> str:
    """Relpath (under the project source_root) of a job's output file.

    PANDDA_JOBS_ROOT defaults to the data root; for the common case where the
    project source_root is also under there, we store a jobs/<id>/<file> path.
    """
    return f"jobs/{job.id}/{out_name}"
