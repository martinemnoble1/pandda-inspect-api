"""
JobRunner seam — how compute (pandda.analyse, refinement) is launched.

The API hands a :class:`JobSpec` to a :class:`JobRunner` and gets back an
opaque handle it polls; it never knows *how* the job runs. A JobSpec describes
*what* to compute (resolved input paths + params), never *where* / *how* — so
the same request maps onto a local process, a docker sidecar, qsub/SLURM, Azure
Batch, or a CCP4Cloud executor without the contract above the seam changing.

:class:`LocalProcessRunner` is the laptop binding. It is real (it shells out),
not a stub, but it deliberately uses a **status-file wrapper** rather than live
PID introspection (DESIGN §5.1): ``submit`` spawns a small shell wrapper that
activates the environment, runs the tool, and writes ``status.json`` +
``job.log`` into the job dir; ``status`` is a pure read of that file. This is
stateless, survives a server restart, and is the SAME mechanism the
docker-compose ``SharedVolumeRunner`` will use — so the laptop runner
pre-proves the compose binding.

The hardest real-world wrinkle (DESIGN §5.6): ``giant.quick_refine`` is NOT a
bare-PATH binary. The one we want ships with PanDDA2 inside a conda env and
needs CCP4 set up too — and CCP4 ALSO ships an older PanDDA1 giant.refine.
So the wrapper sources CCP4 FIRST then activates the conda env (PanDDA2 wins),
using a host-specific prologue supplied via settings/env, never hardcoded.
"""
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from django.conf import settings


@dataclass
class JobSpec:
    tool: str  # e.g. "giant.quick_refine"
    # Resolved absolute input paths the tool needs. The VIEW resolves
    # dataset -> artifact -> path and fills this; the runner stays DB-free and
    # the HTTP request stays path-free.
    inputs: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    # Note: no scheduler flags, no "where" — those belong to the JobRunner.


class JobRunner(Protocol):
    def probe(self) -> dict: ...
    def submit(self, spec: JobSpec, workdir: Path) -> str: ...
    def status(self, handle: str) -> dict: ...
    def cancel(self, handle: str) -> None: ...


def _activation_prologue() -> str:
    """Shell snippet that prepares the environment for the refinement tool.

    CCP4 FIRST, THEN the PanDDA2 conda env, so the PanDDA2
    ``giant.quick_refine`` takes precedence over CCP4's PanDDA1
    ``giant.refine`` (DESIGN §5.6). Each piece is optional: unset settings
    simply skip that activation, which is what
    makes a CCP4-free dev/test environment work (empty prologue + a stand-in
    REFINE_TOOL exercises the same code path).
    """
    lines = []
    ccp4 = settings.CCP4_SETUP_SH
    conda_sh = settings.CONDA_SH
    conda_env = settings.PANDDA2_CONDA_ENV
    if ccp4:
        lines.append(f". {shlex.quote(ccp4)}")
    if conda_sh and conda_env:
        lines.append(f". {shlex.quote(conda_sh)}")
        lines.append(f"conda activate {shlex.quote(conda_env)}")
    return "\n".join(lines)


class LocalProcessRunner:
    """Run a job as a detached subprocess, reporting via a status file."""

    STATUS = "status.json"
    LOG = "job.log"

    def probe(self) -> dict:
        """Dry-run the activation + check the tool resolves AFTER it.

        Returns ``{"available": bool, "tool": str, "resolved": str, "reason":
        str}``. This is the gate (DESIGN §5.6) — NOT ``shutil.which``, which
        would find CCP4's PanDDA1 tool before activation and lie.
        """
        tool = settings.REFINE_TOOL
        script = f"{_activation_prologue()}\ncommand -v {shlex.quote(tool)}\n"
        try:
            proc = subprocess.run(
                ["/bin/sh", "-c", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "available": False, "tool": tool, "resolved": "",
                "reason": f"probe failed to run: {exc}",
            }
        resolved = proc.stdout.strip()
        if proc.returncode == 0 and resolved:
            return {
                "available": True, "tool": tool, "resolved": resolved,
                "reason": "",
            }
        return {
            "available": False, "tool": tool, "resolved": "",
            "reason": (
                f"'{tool}' not found after activation; check CCP4_SETUP_SH / "
                f"CONDA_SH / PANDDA2_CONDA_ENV (stderr: {proc.stderr.strip()})"
            ),
        }

    def submit(self, spec: JobSpec, workdir: Path) -> str:
        """Write a wrapper + initial status, spawn it detached, return handle.

        ``workdir`` is the job's own dir (created by the caller under
        PANDDA_JOBS_ROOT). The returned handle is the workdir path; ``status``
        reads ``status.json`` from it — stateless across requests/restarts.
        """
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        self._write_status(workdir, "running", None, {})

        argv, stdin = self._build_argv(spec, workdir)
        wrapper = self._wrapper_script(argv, stdin, workdir)
        wrapper_path = workdir / "run.sh"
        wrapper_path.write_text(wrapper, encoding="utf-8")

        log = open(workdir / self.LOG, "wb")
        # start_new_session detaches from the request process group so the job
        # outlives the HTTP request (and a dev autoreload).
        subprocess.Popen(
            ["/bin/sh", str(wrapper_path)],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(workdir),
            start_new_session=True,
        )
        return str(workdir)

    def status(self, handle: str) -> dict:
        """Pure read of the job's status.json. Missing ⇒ still running."""
        path = Path(handle) / self.STATUS
        if not path.is_file():
            return {"state": "running", "exit_code": None, "outputs": {}}
        import json

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {"state": "running", "exit_code": None, "outputs": {}}

    def cancel(self, handle: str) -> None:
        """Terminate the job's process group, then mark it failed."""
        pgid_file = Path(handle) / "pgid"
        if pgid_file.is_file():
            try:
                pgid = int(pgid_file.read_text())
                os.killpg(pgid, signal.SIGTERM)
            except (ValueError, OSError, ProcessLookupError):
                pass
        self._write_status(Path(handle), "failed", -1, {}, note="cancelled")

    # --- internals --------------------------------------------------------

    # The output file basename (under workdir) the wrapper looks for, per tool.
    # servalcat/refmac both write <prefix>.pdb/.mtz; we standardise prefix.
    OUT_PREFIX = "refine"

    def _build_argv(self, spec: JobSpec, workdir: Path) -> list:
        """Map a JobSpec to the refinement tool's command line.

        Drives stock CCP4 directly (DESIGN §5.8): servalcat by default, refmac5
        as fallback — NOT giant.quick_refine (a non-reproducible wrapper). The
        tool is chosen by the trailing component of ``spec.tool`` so an
        absolute path or a bare name both work; an unrecognised tool falls back
        to a generic ``servalcat``-style call. Inputs are pre-resolved absolute
        paths (the view's job); outputs land in ``workdir`` under OUT_PREFIX.
        """
        tool = spec.tool or settings.REFINE_TOOL
        name = Path(tool).name
        pdb = spec.inputs.get("pdb", "")
        mtz = spec.inputs.get("mtz", "")
        cif = spec.inputs.get("cif", "")
        out = str(workdir / self.OUT_PREFIX)
        ncycle = str(spec.params.get("ncycle", 10))

        if name in ("refmac5", "refmac"):
            # Classic refmac: file args on the command line, keywords on stdin
            # (NCYC / END). Returned stdin is fed via heredoc by the wrapper.
            argv = [
                tool,
                "XYZIN", pdb, "HKLIN", mtz,
                "XYZOUT", f"{out}.pdb", "HKLOUT", f"{out}.mtz",
            ]
            if cif:
                argv += ["LIBIN", cif]
            stdin = f"NCYC {ncycle}\nEND\n"
            return argv, stdin

        # Default: servalcat crystallographic refinement (modern CCP4).
        # --source xray is required for X-ray data (vs electron/neutron).
        argv = [
            tool, "refine_xtal_norefmac",
            "--model", pdb,
            "--hklin", mtz,
            "--source", "xray",
            "-o", out,
            "--ncycle", ncycle,
        ]
        if cif:
            argv += ["--ligand", cif]
        # Pass through extra servalcat flags from params (skip mapped ones).
        for k, v in spec.params.items():
            if k == "ncycle":
                continue
            argv += [f"--{k}", str(v)]
        return argv, ""

    def _wrapper_script(
        self, argv: list, stdin: str, workdir: Path
    ) -> str:
        """The wrapper: activate env, record pgid, run tool, write status.

        Writing status.json (with the real exit code + discovered outputs) is
        the job's last act, so a present-and-complete status.json is the
        single source of truth for ``status`` (DESIGN §5.1). ``stdin`` (refmac
        keywords) is fed via a heredoc when non-empty; servalcat passes "".
        """
        cmd = " ".join(shlex.quote(a) for a in argv)
        # Feed refmac keywords on stdin only when present.
        if stdin:
            run_line = f"{cmd} <<'REFMAC_KEYWORDS'\n{stdin}REFMAC_KEYWORDS"
        else:
            run_line = cmd
        status_path = workdir / self.STATUS
        wd = str(workdir)
        # Output discovery: both servalcat (-o refine) and refmac (XYZOUT
        # refine.pdb) write under OUT_PREFIX; servalcat may suffix, so glob.
        # Record the basename relative to workdir.
        return f"""#!/bin/sh
# Record our own process-group id so cancel() can signal the whole tree.
echo $$ > {shlex.quote(wd + '/pgid')}
{_activation_prologue()}
{run_line}
rc=$?
out_pdb=""; for f in "{wd}/{self.OUT_PREFIX}"*.pdb; do \
  [ -f "$f" ] && out_pdb=$(basename "$f"); done
out_mtz=""; for f in "{wd}/{self.OUT_PREFIX}"*.mtz; do \
  [ -f "$f" ] && out_mtz=$(basename "$f"); done
if [ "$rc" -eq 0 ]; then state="succeeded"; else state="failed"; fi
cat > {shlex.quote(str(status_path))} <<JSON
{{"state": "$state", "exit_code": $rc,
  "outputs": {{"pdb": "$out_pdb", "mtz": "$out_mtz"}}}}
JSON
"""

    def _write_status(
        self, workdir: Path, state: str, exit_code, outputs: dict, note=""
    ) -> None:
        import json

        payload = {"state": state, "exit_code": exit_code, "outputs": outputs}
        if note:
            payload["note"] = note
        (workdir / self.STATUS).write_text(
            json.dumps(payload), encoding="utf-8"
        )


def get_runner() -> JobRunner:
    """The configured runner. Only the local binding exists here."""
    return LocalProcessRunner()


# Future: SharedVolumeRunner (compose), QsubRunner, SlurmRunner,
# AzureBatchRunner, CCP4CloudRunner — same protocol.
