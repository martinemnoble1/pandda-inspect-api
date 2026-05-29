"""
JobRunner seam — how compute (pandda.analyse, refinement) is launched.

The second pluggable interface. The API hands a JobSpec to a JobRunner and gets
back a handle it can poll; it never knows *how* the job runs. A JobSpec
describes *what* to compute, never *where* — so the same request maps onto a
local process, qsub/SLURM, Azure Batch, or a CCP4Cloud executor.

Only the local-detached-process stub is sketched; nothing is wired to a real
scheduler in this reference.
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class JobSpec:
    tool: str  # e.g. "pandda.analyse", "giant.quick_refine"
    inputs: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    # Note: no paths, no scheduler flags — those belong to the JobRunner.


class JobRunner(Protocol):
    def submit(self, spec: JobSpec) -> str: ...
    def status(self, job_id: str) -> dict: ...
    def cancel(self, job_id: str) -> None: ...


class LocalProcessRunner:
    """Stub: would launch a detached local process per JobSpec."""

    def submit(self, spec: JobSpec) -> str:
        raise NotImplementedError(
            "Reference stub — wire to subprocess for local execution."
        )

    def status(self, job_id: str) -> dict:
        raise NotImplementedError

    def cancel(self, job_id: str) -> None:
        raise NotImplementedError


# Future: QsubRunner, SlurmRunner, AzureBatchRunner, CCP4CloudRunner.
