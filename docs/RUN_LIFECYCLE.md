# ADR: PanDDA run-lifecycle ownership in pandda-inspect-api

- **Status:** Under design (accepted in principle; v0 contract drafted here).
- **Date:** 2026-06-06.
- **Participants:** Reinspect side (this repo) + Materia/CCP4i2 side.
- **Supersedes:** the §9 split in Materia's `PANDDA2_ON_AZURE.md` (Materia
  submits Batch; Reinspect only ingests/reviews).
- **Relates to:** [MATERIA_INTEGRATION.md](MATERIA_INTEGRATION.md) (R0/R2/R3/R5/R6)
  and the `Q2` gate ("where does PanDDA2 run?"). **This ADR answers Q2.**

## Context

Reinspect already owns the *back half* of the PanDDA pipeline: ingest
(`ingest_pandda2`) and the event-review UX. The original plan had Materia own
the *front half* (trigger + Batch submission) and hand Reinspect a finished
`pandda2_out/` tree. This ADR moves the whole run lifecycle — **trigger →
submit → monitor → ingest → review** — into Reinspect. Materia's role collapses
to a "Run PanDDA" button that calls `export_pandda`, unzips to the share, POSTs
a run-request, and redirects the user to Reinspect.

Why the shift (in priority order):

1. **PanDDA-domain vocabulary belongs next to code that already speaks it.**
   Run-progress parsing (shell N/M), failure classification (OOM, missing
   free-R, ligand block, dataset-range), and recovery actions ("retry on a
   larger SKU") draw on the same lexicon Reinspect uses for events/shells/
   autobuild. Duplicating that dialect in Materia guarantees drift.
2. **The recovery surface composes with the review surface.** A failed-OOM
   retry button and a "review these events" link want to live in the same UI,
   next to the events grid.
3. **Materia is a project bench, not a compute orchestrator.** Keeping it a
   host that hands off to specialised tools is the cleaner bounded context.

This is not scope grafted on: it is the **`JobRunner` seam (R5) used as
designed** — `submit`/`status`/`cancel` with an opaque handle and
output-as-foreign-ref — with Q2 answered as "Reinspect submits the run."

## Decision

Reinspect owns the run lifecycle, exposed as a `Run` aggregate and a
`POST /runs/` entry point. Three conditions are **first-class requirements**,
not follow-ups.

### Condition (a) — Batch stays behind the runner seam

Cloud specifics live in **one** `AzureBatchRunner` implementing the existing
`jobs.JobRunner` protocol (`probe`/`submit`/`status`/`cancel`), selected by a
`PANDDA_JOB_RUNNER` env factory — exactly the discipline PR #11 used for
`PANDDA_DATA_STORE=azure`. The contract above the seam (`POST /runs/`, the
`Run` model) stays **cloud-agnostic**: `sizing_hint` is abstract,
`runner_handle`/`batch_task_id` is opaque (support-only), and **no Azure / SKU
/ pool types appear in the API or in `JobSpec`**. A `LocalProcessRunner` binding
runs `pandda2.analyse` as a detached subprocess (the status-file wrapper that
exists today already does this for refinement) so the **entire trigger → run →
ingest → review loop works on a laptop with zero Azure** — the project's
testability invariant, and it de-risks the contract before Batch exists.

> Implication for Materia: `pandda-batch.bicep` becomes a **Reinspect-runtime
> dependency** consumed by `AzureBatchRunner`. Materia's Container App does not
> know Batch exists. The managed-identity grant (Batch contributor + the share
> mount) moves to Reinspect's identity.

### Condition (b) — logs are pointers, not DB rows

Logs are **not** streamed through the database. They live where the runner
already puts them — Batch `streamFiles` (node-local stdout/stderr via Batch's
own auth, no proxy to operate) for the cloud binding, a local file / ring
buffer for `LocalProcessRunner`. The `Run` row holds a **pointer**
(`log_stream_url`) plus coarse status and shell-progress (low-frequency
writes). This keeps the DB write profile close to today's batch-ingest
workload, so **SQLite stays viable for single-tenant / desktop**. Postgres
becomes the **multi-tenant switch** (multiple concurrent runs × multiple
reviewers) — and since `DATABASES` is already env-driven, that is a config flip,
not a refactor.

### Condition (c) — rerun ⇄ decision reconciliation is a day-1 requirement

Making reruns easy is the *point* of the recovery UX, which means it collides
immediately with the open problem in [CLAUDE.md](../CLAUDE.md): *re-ingest
clobbers decision state (replace, not reconcile)*. The reconciliation model:

- **Merge key = `(dtag, event_idx)`** — PanDDA's own addressing, and already
  this repo's `uniq_dataset_event` constraint on `(dataset, event_num)`.
- **The merge rule = machine-overwrite, human-preserve.** This is the schema's
  existing grain (machine opinion vs human verdict). On a `(dtag, event_idx)`
  match, a re-analysis **overwrites the machine columns** (`score`,
  `interesting`, `z_peak/z_mean`, `bdc`, `build_score`, `rscc`,
  `optimal_contour`, event-map artifacts, …) and **never touches the human
  columns** (`decision`, `confidence`, `comment`, `inspected_by`,
  `inspected_by_oid`, `inspected_at`, `current_model` / pose lineage,
  `pose_merged`). Re-ingest of a successful rerun is therefore a **merge against
  this key**, not a replace.
- **Vanished events are tombstoned, not deleted.** If a previously-reviewed
  `(dtag, event_idx)` is not emitted by the rerun, do **not** cascade-delete it
  (that silently loses a human's decision). Flag it (`inputs_changed`, the
  existing "surface, don't resolve" mechanism, §1.3) and keep it.
- **Moved-peak guard (the subtle one).** `event_idx` is assigned per run; a
  PanDDA2 version bump or a changed dataset set can renumber events. Before
  merging a human decision onto a `(dtag, event_idx)`, cross-check the event's
  peak coordinate against the stored one; if it moved beyond tolerance, flag
  `inputs_changed` rather than silently attributing an old "hit" to a different
  physical density.
- **The merge trigger is scope state, not lineage.** `Run.parent_run` (retry
  lineage) is useful provenance, **but the merge-vs-replace decision keys on
  whether the target `(project, group)` scope already holds human decisions**,
  because the dangerous case — a fresh "Run PanDDA" months later over a
  reviewed group — has **no** `parent_run`. So:
  - fresh run, no prior reviewed events in scope → plain ingest;
  - failure-retry (`parent_run` set, parent `Failed`, no events produced) →
    clobber-safe, nothing to reconcile;
  - any run over a scope with prior reviewed events → **merge** (machine-
    overwrite / human-preserve), tombstone vanished, flag moved peaks.

## The contract — `POST /runs/`

### Request / response

```jsonc
// POST /runs/
{
  "project":    "CDK4CyclinD1",          // Materia's stable slug (external key)
  "group":      "fragment-screen-2026",  // sub-batch label within the project
  "share_path": "/mnt/projects/CDK4CyclinD1/pandda_inputs/fragment-screen-2026",
  "sizing_hint": { "datasets": 120, "cell_volume_class": "large" },  // optional
  "input_hash": "sha256:…",              // manifest-derived; for idempotency
  "retry_of":   null                     // or a prior run_id for an explicit retry
}
// 201 (new) or 200 (idempotent hit)
{ "run_id": "run-2026-06-06-001",
  "ui_url": "https://reinspect.example.com/runs/run-2026-06-06-001",
  "status": "queued" }
```

### Project resolution — decision: external key + get-or-create

Materia sends its **own stable slug** as `project`; Reinspect stores it as
`Project.external_id` (namespaced, unique) and **owns its own PKs**. Materia
never holds a Reinspect integer FK. `POST /runs/` **get-or-creates** the
`Project` by `external_id` on first run.

This collapses Materia's two options into the simplest thing: no provisioning
round-trip (option 1's cost) and no ID Materia must pre-store (option 2's cost).
It is option (2) — *external_id* — minus the "Materia stores our id" coupling.
Assumes one Reinspect `Project` per Materia project (1:1); if a Reinspect
project ever needs multiple Materia parents, revisit.

### Idempotency — decision: input-hash key, explicit retry escape hatch

`idempotency_key = sha256(project ":" group ":" input_hash)`, **unique** on
`Run`. A duplicate POST (double-click, Materia retry) returns the existing
`run_id` (201 → 200) — Materia's button is idempotent for free. **An explicit
retry is a deliberate new run**: it carries `retry_of: <run_id>` and is **never
deduped** against its parent (it sets `parent_run` and gets a fresh row). This
keeps `parent_run_id` *out* of the hash (avoiding the chicken-and-egg of
hashing a lookup) while still stopping a retry from collapsing onto the failed
run — equivalent to Materia's "input-hash + parent" intent, simpler mechanics.

### Provenance — `triggered_by_oid` via on-behalf-of

The trigger carries the **human's** AAD identity (OBO), forwarded by Materia's
backend proxy (the `api/proxy/ccp4i2/*` pattern), not a service principal — so
provenance is continuous from trigger → decision. `Run.triggered_by_oid` is the
sibling of the `Event.inspected_by_oid` landed in PR #11. (Edge case, correct as
designed: if the triggering user is deactivated mid-run, `triggered_by_oid`
stays a valid historical claim; `inspected_by_oid` on later decisions stays
empty until someone reviews — the chain diverges, it does not break.)

## The `Run` model (sketch)

`Run` is a **distinct aggregate from `Job`**: `Job` produces exactly one output
`Artifact` and repoints a `current_model`; a PanDDA run produces a whole
`pandda2_out/` **tree** that is then *ingested* into many Datasets/Events/
Artifacts. They are **siblings sharing the `JobRunner` protocol**, not the same
model.

```python
class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROVISIONING = "provisioning", "Provisioning"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    project    = models.ForeignKey(Project, on_delete=models.CASCADE,
                                   related_name="runs")
    group      = models.CharField(max_length=255)
    share_path = models.CharField(max_length=1024)   # input dir → ingest source_root
    out_dir    = models.CharField(max_length=1024, blank=True, default="")  # pandda2_out

    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.QUEUED)
    # Machine classification — advisory, distinct from any human read, like
    # Event.score/interesting. Catalogue-coded; see below.
    failure_mode    = models.CharField(max_length=64, blank=True, default="")
    failure_message = models.CharField(max_length=512, blank=True, default="")
    failure_detail  = models.TextField(blank=True, default="")   # stderr excerpt

    # Opaque runner handle (e.g. a Batch task id). The API never interprets it.
    runner_handle  = models.CharField(max_length=255, blank=True, default="")
    log_stream_url = models.CharField(max_length=1024, blank=True, default="")

    # Provenance + idempotency + retry lineage.
    triggered_by_oid = models.CharField(max_length=255, null=True, blank=True)
    idempotency_key  = models.CharField(max_length=80, unique=True)
    parent_run       = models.ForeignKey("self", null=True, blank=True,
                                         on_delete=models.SET_NULL,
                                         related_name="retries")

    sizing_hint  = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    started_at   = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
```

A PanDDA run is dispatched as a `jobs.JobSpec(tool="pandda2.analyse",
inputs={"data_dirs": …}, params={"dataset_range": …, regexes…, "local_cpus": N})`
through `get_runner().submit(spec, workdir)`; `runner_handle` is the returned
opaque string; a poller maps `JobRunner.status(handle)` → `Run.status`. On
`Succeeded`, the existing `ingest_pandda2` reads `out_dir` (its `source_root` is
the share — the R6 read path already supports in-place trees) under the
reconciliation rule above.

## Failure-mode catalogue — data, not code

Declarative `(pattern → code → recovery-prompt)`, evolvable without redeploys;
Materia contributes patterns, Reinspect owns the table. The classifier output is
**advisory provenance, overridable — never truth** (the machine-opinion grain).
Classification is **pinned to the PanDDA image version** (stderr regexes are
brittle across builds); `unclassified_crash` is a first-class, non-embarrassing
state. Seed catalogue (from Materia's proposal §3.2/§8):

| code | trigger | recovery prompt |
|------|---------|-----------------|
| `oom` | `MemoryError` / SIGKILL on stderr | Retry on a larger SKU |
| `free_r_label` | `No RFree Flag found!` | Re-export to relabel free-R columns |
| `ligand_block` | `KeyError: "block 'comp_*'"` | Update the pinned PanDDA image |
| `dataset_range_zeroed` | `0/N datasets passed range filter` | Re-export with synthetic xtal-NNNN names |
| `ray_scratch_full` | `No space left on device` | Increase node `RAY_TMPDIR` disk |
| `ccp4_missing` | `rhofit: command not found` | Image misconfig — CCP4 didn't source |
| `unclassified_crash` | non-zero exit, no match | Inspect log; open issue if reproducible |

## Sequencing

1. **Contract + `Run` model + `LocalProcessRunner` for `pandda2.analyse`** —
   proves the whole loop end-to-end with **zero Azure**. Materia's button can be
   developed against this (proxy to a local Reinspect). *Small*: a new `JobSpec`
   branch + `Run` model + `/runs/` view.
2. **`AzureBatchRunner` behind the `PANDDA_JOB_RUNNER` factory** — the lockstep
   point. Needs Materia's identity grant + Batch pool; deploy together behind a
   shared "ready" check.
3. **Failure catalogue + recovery UX** — Reinspect-led; Materia's button just
   keeps working.

## What Reinspect needs from Materia (invocation contract)

To be delivered as Materia's `CCP4I2_PANDDA_INVOCATION_CONTRACT.md`:

- **Command:** `pandda2.analyse --data_dirs <in> --out_dir <out> --local_cpus N`
  plus the input regexes (`--pdb_regex final.pdb`, `--mtz_regex final.mtz`,
  `--ligand_cif_regex dict.cif`, `--ligand_pdb_regex ligand.pdb`) and a wide
  defensive `--dataset_range`.
- **Env:** CCP4 sourced + conda env on PATH (image entrypoint); `RAY_TMPDIR`
  set per task.
- **Output:** `--out_dir <share_path>/pandda_results/<group>` (sibling of the
  inputs under the same share root); `pandda2_out/` is a child of `--out_dir`.
- **Exit code:** 0 success / non-zero failure; the runner classifies on stderr
  match, not on the exit-code *value* (advisory-provenance framing).
- **Per-shell progress — OPEN.** Preferred resolution: instrument the fork with
  `print(f"PANDDA_PROGRESS: shell {i}/{N}", flush=True)` so progress is a stable
  contract, not a fragile regex. Until then the runner treats progress as
  unknown (`Running`, no shell count).
- **Pinned image tag** (`materia/pandda:<timestamp>`) — classification rules pin
  to it.

## Open questions

- Final shape of the **moved-peak tolerance** for the reconciliation guard.
- Whether **cancellation** is exposed in v0 (`JobRunner.cancel` exists; the
  question is the UX and whether Batch task termination is wired day 1).
- **Cost/quota governance** for the Batch pool (autoscale caps, budget) — owned
  Materia-side via `pandda-batch.bicep`, surfaced by `Run` telemetry.

## Non-goals (for now)

- Routing the refinement **run-path** for a non-local store through Batch — the
  per-event refinement staging is a separate piece (still the Q2-gated run-path
  in MATERIA_INTEGRATION.md; this ADR covers the *analysis* run, which writes to
  the share that ingest reads in place).
- A generic multi-tool run service — this is PanDDA-specific by design.
