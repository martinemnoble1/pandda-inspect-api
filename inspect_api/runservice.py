"""
Run lifecycle service — submit a PanDDA analysis run and poll it.

Mirrors :mod:`jobservice` (which does the same for per-event refinement) but for
the whole-group *run*: it resolves the project, enforces idempotency, dispatches
a ``pandda2.analyse`` :class:`~inspect_api.jobs.JobSpec` through the
:class:`~inspect_api.jobs.JobRunner` seam, and on success ingests the produced
``pandda2_out/`` tree via the existing reconcile path (which already preserves
human decisions on re-ingest — the merge half of the reconciliation rule in
docs/RUN_LIFECYCLE.md). The HTTP layer (views.RunViewSet) stays thin; this
module owns the lifecycle. No cloud specifics here — the binding is chosen by
``get_runner()`` (PANDDA_JOB_RUNNER).
"""
import hashlib
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .jobs import JobSpec, get_runner
from .models import Project, Run


class RunError(Exception):
    """A run could not be submitted (bad inputs, unknown retry parent)."""


# --- failure-mode catalogue (DATA, not code) ------------------------------
# (compiled-regex, code, user-facing message). Advisory machine classification,
# distinct from any human verdict; pinned conceptually to the PanDDA image
# version. Reinspect owns evolving this; Materia contributes patterns. See
# docs/RUN_LIFECYCLE.md. First match wins; falls back to unclassified_crash.
FAILURE_CATALOGUE = [
    (re.compile(r"MemoryError|Killed|signal 9|SIGKILL"), "oom",
     "Out of memory — retry on a larger SKU"),
    (re.compile(r"No RFree Flag found"), "free_r_label",
     "Missing free-R label — re-export to relabel free-R columns"),
    (re.compile(r"KeyError:\s*[\"']block 'comp_"), "ligand_block",
     "Ligand restraint block missing — update the pinned PanDDA image"),
    (re.compile(r"0/\d+ datasets|passed.*range filter|no datasets"),
     "dataset_range_zeroed",
     "No datasets passed the range filter — re-export with xtal-NNNN names"),
    (re.compile(r"No space left on device"), "ray_scratch_full",
     "Scratch disk full — increase the node's RAY_TMPDIR disk"),
    (re.compile(r"command not found|rhofit: not found"), "ccp4_missing",
     "Tool not on PATH — image misconfigured (CCP4 didn't source)"),
]


def classify_failure(log_text: str):
    """Map a captured log to ``(code, message)`` from the catalogue."""
    for pattern, code, message in FAILURE_CATALOGUE:
        if pattern.search(log_text):
            return code, message
    return "unclassified_crash", "Run failed — inspect the log"


# The image emits ``PANDDA_PROGRESS: dataset <j>/<N>`` on the visible outer
# (per-dataset) iteration. Granularity-neutral capture: keep whatever trails
# the marker (dataset today, possibly shells later) — last line wins.
_PROGRESS = re.compile(r"PANDDA_PROGRESS:\s*(.+)")


def parse_progress(log_text: str) -> str:
    """Latest progress string from the log, or '' if none emitted yet."""
    matches = _PROGRESS.findall(log_text)
    return matches[-1].strip()[:32] if matches else ""


def _idempotency_key(external_id: str, group: str, input_hash: str) -> str:
    raw = f"{external_id}:{group}:{input_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _default_out_dir(share_path: str, group: str) -> str:
    """Where pandda2_out/ is written, per the invocation contract.

    Convention: a ``pandda_inputs/<group>`` input dir maps to a sibling
    ``pandda_results/<group>``; anything else falls back to ``<share>/pandda2_out``.
    One place to adjust when Materia finalises the contract.
    """
    parts = list(Path(share_path).parts)
    if "pandda_inputs" in parts:
        parts[parts.index("pandda_inputs")] = "pandda_results"
        return str(Path(*parts))
    return str(Path(share_path) / "pandda2_out")


# pandda2.analyse overrides a trigger may set. Only these keys reach the
# command line — an allowlist (not for shell safety: values are argv-quoted,
# but to stop arbitrary --flags) that is settings-extensible
# (PANDDA_RUN_ALLOWED_PARAMS) as more pandda2 flags are confirmed. out_dir /
# data_dirs are deliberately NOT here — they are server-computed (ingest reads
# out_dir). See docs/RUN_LIFECYCLE.md (invocation contract).
DEFAULT_ALLOWED_PARAMS = frozenset({
    "pdb_regex", "mtz_regex", "ligand_cif_regex", "ligand_pdb_regex",
    "dataset_range", "local_cpus",
})


def allowed_params() -> frozenset:
    extra = getattr(settings, "PANDDA_RUN_ALLOWED_PARAMS", None)
    return frozenset(extra) if extra else DEFAULT_ALLOWED_PARAMS


def submit_run(
    *,
    project_external_id: str,
    group: str,
    share_path: str,
    input_hash: str = "",
    sizing_hint: dict | None = None,
    params: dict | None = None,
    retry_of=None,
    triggered_by_oid: str | None = None,
):
    """Create (or idempotently return) a Run and dispatch it.

    Returns ``(run, created)``. A duplicate of the SAME intent (same
    project/group/input_hash, no ``retry_of``) returns the existing run with
    ``created=False``. An explicit ``retry_of`` is always a new run.
    """
    if not project_external_id or not group or not share_path:
        raise RunError("project, group and share_path are required")

    params = params or {}
    bad = set(params) - allowed_params()
    if bad:
        raise RunError(
            f"unknown run params {sorted(bad)}; allowed: "
            f"{sorted(allowed_params())}"
        )

    parent = None
    if retry_of is not None:
        parent = Run.objects.filter(pk=retry_of).first()
        if parent is None:
            raise RunError(f"retry_of references unknown run {retry_of}")

    base_key = _idempotency_key(project_external_id, group, input_hash)
    if parent is None:
        existing = Run.objects.filter(idempotency_key=base_key).first()
        if existing is not None:
            return existing, False
        key = base_key
    else:
        # Explicit retry: never dedupe against the parent — fresh unique key.
        key = f"{base_key}:retry:{uuid.uuid4().hex}"

    # Create the output dir's LEAF, but require its parent to already exist —
    # a missing parent means the share isn't mounted or share_path is wrong, so
    # fail loudly here (clear 400) rather than dispatch a run that dies
    # cryptically on the node. We deliberately do NOT mkdir -p: silently
    # building a deep tree would mask a bad path.
    out_dir = Path(_default_out_dir(share_path, group))
    if not out_dir.parent.is_dir():
        raise RunError(
            f"output parent {out_dir.parent} does not exist — is the share "
            "mounted and share_path correct?"
        )
    out_dir.mkdir(exist_ok=True)

    project, _ = Project.objects.get_or_create(
        external_id=project_external_id,
        defaults={"name": project_external_id, "source_root": share_path},
    )

    run = Run.objects.create(
        project=project,
        group=group,
        share_path=share_path,
        out_dir=str(out_dir),
        sizing_hint=sizing_hint or {},
        params=params,
        idempotency_key=key,
        parent_run=parent,
        triggered_by_oid=triggered_by_oid,
        status=Run.Status.QUEUED,
    )

    _dispatch(run)
    return run, True


def _dispatch(run: Run) -> None:
    """Build the JobSpec and hand it to the runner; record handle/status."""
    workdir = Path(settings.PANDDA_JOBS_ROOT) / "runs" / str(run.id)
    spec = JobSpec(
        tool="pandda2.analyse",
        inputs={"data_dirs": str(Path(run.share_path) / "datasets")},
        # Server-computed out_dir + the trigger's allowlisted overrides.
        params={"out_dir": run.out_dir, **run.params},
        # Resource hint for the runner to size compute (e.g. --local_cpus).
        sizing_hint=run.sizing_hint or {},
    )
    try:
        handle = get_runner().submit(spec, workdir)
    except Exception as exc:  # noqa: BLE001 - surface ANY dispatch failure
        run.status = Run.Status.FAILED
        run.failure_mode = "dispatch_error"
        run.failure_message = f"Could not submit run: {exc}"
        run.completed_at = timezone.now()
        run.save(update_fields=[
            "status", "failure_mode", "failure_message", "completed_at",
        ])
        return
    run.runner_handle = handle
    run.log_stream_url = f"file://{workdir / 'job.log'}"
    run.status = Run.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=[
        "runner_handle", "log_stream_url", "status", "started_at",
    ])


_TERMINAL = (Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELLED)


def refresh_run(run: Run) -> Run:
    """Poll the runner; on first success ingest the tree. Idempotent."""
    if run.status in _TERMINAL or not run.runner_handle:
        return run

    st = get_runner().status(run.runner_handle)
    state = st.get("state")
    # Runner-agnostic: a remote runner (Batch) surfaces the log text + a log
    # pointer in the status dict; the local runner returns neither, so we fall
    # back to reading its on-disk job.log. Either way progress + failure
    # classification run on the same text.
    log_text = st.get("log") or _read_log(run)
    save_fields = []
    log_url = st.get("log_url")
    if log_url and log_url != run.log_stream_url:
        run.log_stream_url = log_url
        save_fields.append("log_stream_url")

    if state == "running":
        progress = parse_progress(log_text)
        if progress and progress != run.progress:
            run.progress = progress
            save_fields.append("progress")
        if save_fields:
            run.save(update_fields=save_fields)
        return run
    if save_fields:
        run.save(update_fields=save_fields)
    if state == "failed":
        return _fail(run, log_text, st.get("failure_message"))
    if state == "succeeded":
        return _complete(run)
    return run


def _read_log(run: Run) -> str:
    """Best-effort tail of the captured stdout/stderr for classification."""
    handle = run.runner_handle
    if not handle:
        return ""
    log = Path(handle) / "job.log"
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _fail(run: Run, log_text: str = None, explicit_message: str = None) -> Run:
    if log_text is None:
        log_text = _read_log(run)
    code, message = classify_failure(log_text)
    # A scheduling/infra failure (e.g. Batch task that never produced stdout)
    # has no log to match; prefer the runner's own message then.
    if not log_text and explicit_message:
        message = explicit_message
    run.status = Run.Status.FAILED
    run.failure_mode = code
    run.failure_message = message
    run.failure_detail = (log_text or explicit_message or "")[-4000:]
    run.completed_at = timezone.now()
    run.save(update_fields=[
        "status", "failure_mode", "failure_message", "failure_detail",
        "completed_at",
    ])
    return run


def _complete(run: Run) -> Run:
    """Ingest the produced tree, then mark the run succeeded.

    Ingest reuses the management command, whose reconcile path preserves human
    decisions on re-ingest (the merge half of reconciliation). If the expected
    PanDDA2 output isn't present, the run is failed with a clear message rather
    than silently 'succeeding' with nothing ingested.
    """
    events_csv = (
        Path(run.out_dir) / "analyses" / "pandda_analyse_events.csv"
    )
    if not events_csv.is_file():
        run.status = Run.Status.FAILED
        run.failure_mode = "no_output_tree"
        run.failure_message = (
            f"Run finished but no PanDDA2 output at {run.out_dir}"
        )
        run.completed_at = timezone.now()
        run.save(update_fields=[
            "status", "failure_mode", "failure_message", "completed_at",
        ])
        return run

    call_command(
        "ingest_pandda2", project=run.project.name, root=run.out_dir
    )
    run.status = Run.Status.SUCCEEDED
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "completed_at"])
    return run
