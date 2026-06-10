"""
Azure Batch binding of the JobRunner seam — step 2 of docs/RUN_LIFECYCLE.md.

Submits a ``pandda2.analyse`` run as a Batch task against Materia's pool and
maps the task lifecycle back onto the normalised ``status`` dict the rest of the
system already understands (the same contract LocalProcessRunner returns). It is
selected by ``PANDDA_JOB_RUNNER=azure_batch``; nothing above the seam changes.

Design notes:
- **All SDK calls are isolated and lazily imported.** The module imports no
  ``azure`` at top level, so importing it (and the pure helpers below) is free
  even where ``azure-batch`` isn't installed. The client is injectable, so the
  lifecycle logic is unit-testable against a fake client with no Azure at all.
- **The pure helpers** (state mapping, handle (de)serialisation, log pointer)
  carry the real decisions and are fully tested. The thin SDK layer
  (``create_job``/``create_task``/``get_task``/``terminate_task``/
  ``download_task_file``) is written against azure-batch 15.x (``BatchClient``)
  but **must be validated against a live Batch account** before production —
  the one thing the mocked tests cannot cover.
- Auth is ``DefaultAzureCredential`` → the Container App's managed identity
  (granted Batch contributor). Config from the ``AZURE_BATCH_*`` env vars.
- The run writes ``pandda2_out/`` to the shared mount that BOTH the Batch node
  and the Reinspect container see, so ingest-on-success reads it in place.
"""
import os
import re
import shlex
from pathlib import Path

# Batch task states that mean "still going" (BatchTaskState: active / preparing
# / running / completed). Anything not terminal maps to our "running".
_RUNNING_STATES = {"active", "preparing", "running"}

# Sentinel for "pool container config not yet looked up" (None is a valid
# result meaning "non-container pool").
_UNSET = object()

# Default container run options. A container-enabled Batch node mounts the
# share (e.g. at /mnt/projects) via the pool startTask; the task container must
# bind-mount it to see the same tree. Overridable per deployment.
_DEFAULT_RUN_OPTIONS = "-v /mnt/projects:/mnt/projects"


def split_handle(handle: str):
    """``"<job_id>/<task_id>"`` -> ``(job_id, task_id)``."""
    job_id, _, task_id = (handle or "").partition("/")
    return job_id, task_id


def make_handle(job_id: str, task_id: str) -> str:
    return f"{job_id}/{task_id}"


def log_pointer(endpoint: str, job_id: str, task_id: str) -> str:
    """The Batch file-API reference to the task's stdout.

    A *pointer*, not a browser-fetchable URL — reading it needs Batch auth, so
    live log streaming to the UI is a Reinspect-proxied concern (step 3). Stored
    on ``Run.log_stream_url`` for support/diagnostics meanwhile.
    """
    base = (endpoint or "").rstrip("/")
    return f"{base}/jobs/{job_id}/tasks/{task_id}/files/stdout.txt"


# cell_volume_class -> how to derive --local_cpus from the node's vCPU count.
# Large/huge cells are RAM-hungry per PanDDA2 worker, so we leave headroom
# rather than using every core — but PROPORTIONAL to node size, not a fixed
# floor. (Before the autobuild memory fix these were hard-capped at 2/1
# regardless of core count, to survive the phantom-ligand-as-whole-complex
# blow-up — fix 922f7027. With that ingestion fix on-branch and
# PANDDA_LOCAL_AUTOBUILD=1 bounding per-conformer peak memory, the per-worker
# footprint is well within budget — an 8-CPU local run on CDK4/CyclinD1 ran
# clean, and 14/16 on Standard_E16ds_v4 is comfortable.) AZURE_BATCH_LOCAL_CPUS
# stays as the operator escape hatch.
_CPUS_BY_CELL_CLASS = {
    "large": lambda vcpus: vcpus - 2,
    "huge": lambda vcpus: vcpus // 2,
}


def _vcpus_from_vm_size(vm_size: str):
    """Best-effort vCPU count from an Azure VM size, e.g.
    'Standard_E16ds_v4' -> 16, and constrained 'Standard_E32-8ds_v4' -> 8
    (the usable count). None if it can't be parsed."""
    m = re.match(r"(?i)^standard_[a-z]+(\d+)(?:-(\d+))?", vm_size or "")
    if not m:
        return None
    return int(m.group(2) or m.group(1))


def _pick_cpus(sizing_hint: dict, vm_size: str = None):
    """Default --local_cpus for the Batch node. Large/huge cells leave RAM
    headroom (vcpus-2 / vcpus//2); other classes use the node's full vCPU count
    (so a 16-core box isn't left at PanDDA2's default of 6). None ⇒ omit the
    flag (PanDDA2's own default) — including when vm_size can't be parsed, so a
    headroom class with an unknown node falls back to PanDDA2's default rather
    than a guess. NB: derive from the POOL's vm_size, never os.cpu_count() —
    the runner runs in the Container App, a different machine from the node."""
    vcpus = _vcpus_from_vm_size(vm_size)
    reducer = _CPUS_BY_CELL_CLASS.get((sizing_hint or {}).get(
        "cell_volume_class"))
    if reducer is not None and vcpus is not None:
        return max(1, reducer(vcpus))
    return vcpus


def normalise_task_state(task) -> dict:
    """Map a Batch task object onto the normalised runner-status dict.

    Returns ``{"state": "running"|"succeeded"|"failed", ...}`` — the same shape
    LocalProcessRunner.status returns, so refresh_run is runner-agnostic. Pure:
    takes any object exposing ``.state`` and ``.execution_info`` (the real
    ``BatchTask`` or a test double).
    """
    state = getattr(task, "state", None)
    state = getattr(state, "value", state)  # BatchTaskState enum -> str
    if state in _RUNNING_STATES:
        return {"state": "running"}
    if state == "completed":
        info = getattr(task, "execution_info", None)
        exit_code = getattr(info, "exit_code", None)
        failure = getattr(info, "failure_info", None)
        if failure is None and exit_code == 0:
            return {"state": "succeeded", "exit_code": 0}
        message = (
            getattr(failure, "message", None)
            or f"task exited with code {exit_code}"
        )
        return {
            "state": "failed",
            "exit_code": exit_code,
            "failure_message": message,
        }
    # Unknown / not-yet-populated — treat as still running, never prematurely
    # fail or succeed.
    return {"state": "running"}


class AzureBatchRunner:
    """JobRunner implementation submitting to an Azure Batch pool."""

    def __init__(self, *, client=None, pool_id=None, job_id=None,
                 endpoint=None):
        self.pool_id = pool_id or os.environ.get("AZURE_BATCH_POOL_ID")
        self.job_id = job_id or os.environ.get(
            "AZURE_BATCH_JOB_ID", "pandda-runs"
        )
        self.endpoint = endpoint or os.environ.get(
            "AZURE_BATCH_ACCOUNT_ENDPOINT"
        )
        # Injectable for tests; built lazily from env otherwise.
        self._client = client if client is not None else self._build_client()
        # Cached pool object (None if it can't be fetched); _UNSET until first
        # looked up. Used for both container config and vm_size.
        self._pool_obj = _UNSET

    def _build_client(self):
        if not self.endpoint or not self.pool_id:
            raise RuntimeError(
                "AzureBatchRunner needs AZURE_BATCH_ACCOUNT_ENDPOINT and "
                "AZURE_BATCH_POOL_ID"
            )
        from azure.batch import BatchClient
        from azure.identity import DefaultAzureCredential

        return BatchClient(
            endpoint=self.endpoint, credential=DefaultAzureCredential()
        )

    # --- JobRunner protocol ------------------------------------------------

    def probe(self) -> dict:
        try:
            ok = bool(self._client.pool_exists(self.pool_id))
        except Exception as exc:  # noqa: BLE001 - surface any Batch error
            return {"available": False, "reason": str(exc)}
        return {
            "available": ok,
            "pool": self.pool_id,
            "reason": "" if ok else f"pool {self.pool_id} not found",
        }

    def submit(self, spec, workdir) -> str:
        from azure.batch import models as bm

        self._ensure_job(bm)
        task_id = f"run-{Path(workdir).name}"
        kwargs = {"id": task_id, "command_line": self._command_line(spec)}
        # A container-enabled pool REQUIRES every task to carry its own
        # container settings ("Container-enabled compute node requires task
        # container settings"); a non-container pool gets none.
        container = self._container_settings(bm)
        if container is not None:
            kwargs["container_settings"] = container
        self._client.create_task(
            self.job_id, bm.BatchTaskCreateOptions(**kwargs)
        )
        return make_handle(self.job_id, task_id)

    def status(self, handle: str) -> dict:
        job_id, task_id = split_handle(handle)
        task = self._client.get_task(job_id, task_id)
        result = normalise_task_state(task)
        # Surface the stdout (for progress + failure classification) and a log
        # pointer; refresh_run is written to consume these when present.
        result["log"] = self._read_stdout(job_id, task_id)
        result["log_url"] = log_pointer(self.endpoint, job_id, task_id)
        return result

    def cancel(self, handle: str) -> None:
        job_id, task_id = split_handle(handle)
        self._client.terminate_task(job_id, task_id)

    # --- SDK-isolated helpers ---------------------------------------------

    def _ensure_job(self, bm) -> None:
        """Create the umbrella Batch job bound to the pool, idempotently."""
        from azure.core.exceptions import ResourceExistsError

        try:
            self._client.create_job(
                bm.BatchJobCreateOptions(
                    id=self.job_id,
                    pool_info=bm.BatchPoolInfo(pool_id=self.pool_id),
                )
            )
        except ResourceExistsError:
            pass

    def _command_line(self, spec) -> str:
        from dataclasses import replace

        from .jobs import build_pandda2_argv

        # Size --local_cpus for the Batch node. Precedence:
        # AZURE_BATCH_LOCAL_CPUS (operator escape hatch) > an explicit trigger
        # param > large/huge cell memory cap > the pool VM's vCPU count > omit
        # (PanDDA2's own default). cpu_count() is NOT used — the runner runs in
        # the Container App, not on the Batch node.
        params = dict(spec.params)
        env = os.environ.get("AZURE_BATCH_LOCAL_CPUS")
        if env:
            params["local_cpus"] = int(env)
        elif params.get("local_cpus") is None:
            cpus = _pick_cpus(spec.sizing_hint or {}, self._pool_vm_size())
            if cpus is not None:
                params["local_cpus"] = cpus
        spec = replace(spec, params=params)
        return " ".join(shlex.quote(a) for a in build_pandda2_argv(spec))

    def _pool(self):
        """The pool object, cached. Best-effort (any error → None)."""
        if self._pool_obj is _UNSET:
            try:
                self._pool_obj = self._client.get_pool(self.pool_id)
            except Exception:  # noqa: BLE001 - treat as unknown pool
                self._pool_obj = None
        return self._pool_obj

    def _pool_container_config(self):
        """The pool's container configuration, or None for a non-container
        pool."""
        vmc = getattr(self._pool(), "virtual_machine_configuration", None)
        return getattr(vmc, "container_configuration", None) if vmc else None

    def _pool_vm_size(self):
        """The pool's VM size string (e.g. 'Standard_E16ds_v4'), or None."""
        return getattr(self._pool(), "vm_size", None)

    def _container_settings(self, bm):
        """Task container settings, mirroring the pool's image + registry.

        Returns None for a non-container pool (so the task carries none). The
        image defaults to the pool's first container image (overridable via
        AZURE_BATCH_CONTAINER_IMAGE); the registry is the pool's first
        registry (so ACR auth matches the pool). The bind-mount run options
        default to the share path, overridable via
        AZURE_BATCH_CONTAINER_RUN_OPTIONS.
        """
        image = os.environ.get("AZURE_BATCH_CONTAINER_IMAGE")
        registry = None
        if not image:
            cfg = self._pool_container_config()
            images = getattr(cfg, "container_image_names", None) if cfg else None
            if not images:
                return None  # non-container pool → no task container settings
            image = images[0]
            regs = getattr(cfg, "container_registries", None) or []
            registry = regs[0] if regs else None
        run_options = os.environ.get(
            "AZURE_BATCH_CONTAINER_RUN_OPTIONS", _DEFAULT_RUN_OPTIONS
        )
        return bm.BatchTaskContainerSettings(
            image_name=image,
            container_run_options=run_options,
            registry=registry,
        )

    def _read_stdout(self, job_id: str, task_id: str) -> str:
        try:
            chunks = self._client.download_task_file(
                job_id, task_id, "stdout.txt"
            )
            return b"".join(chunks).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - no stdout yet / not found -> empty
            return ""
