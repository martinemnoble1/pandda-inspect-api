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
import shlex
from pathlib import Path

# Batch task states that mean "still going" (BatchTaskState: active / preparing
# / running / completed). Anything not terminal maps to our "running".
_RUNNING_STATES = {"active", "preparing", "running"}


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
        self._client.create_task(
            self.job_id,
            bm.BatchTaskCreateOptions(
                id=task_id, command_line=self._command_line(spec)
            ),
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
        from .jobs import build_pandda2_argv

        return " ".join(shlex.quote(a) for a in build_pandda2_argv(spec))

    def _read_stdout(self, job_id: str, task_id: str) -> str:
        try:
            chunks = self._client.download_task_file(
                job_id, task_id, "stdout.txt"
            )
            return b"".join(chunks).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - no stdout yet / not found -> empty
            return ""
