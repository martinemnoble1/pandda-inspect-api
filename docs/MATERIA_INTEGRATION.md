# Materia / CCP4i2 integration — the pandda-inspect-api side of the contract

**Status:** Design note. Companion to Materia's
`apps/compounds/docs/proposal/PANDDA_INSPECT_INTEGRATION.md` (the consumer half;
owner: Martin Noble, dated 2026-06-01). **Nothing here has shipped.** This file
records *what this repo must do* to be incorporable by Materia (which embeds
CCP4i2), separating the requirements that are already satisfied from the ones
that are real work — and naming the one that gates the rest.

> **Why this note exists.** The Materia doc names a set of requirements
> (R0–R6) on *this* repo and explicitly says they are "written for the
> pandda-inspect-api side." It also kept gesturing at "whatever integration note
> lands in pandda-inspect-api." This is that note. It was authored *after* a
> review of the Materia doc against the actual code here; the Materia doc has
> since been revised (2026-06-01) to fold in the three corrections below, so the
> two docs now agree. Read the Materia doc for the *consumer* composition (Next
> proxy, Dockerfile, bicep); read this for *our* obligations and their true cost.

---

## TL;DR for anyone working this repo

The integration thesis holds, and better than most: the two seams Materia leans
on (`DataStore`, `JobRunner`) **already exist as `Protocol`s** in this tree, and
`JobSpec`/`runner_handle` are **already** shaped "what, never where/how." So most
of Materia's R-list is *confirmation*, not change. **But:**

1. **R6 is a refactor, not a column** — and it's the load-bearing one. Today
   path resolution happens *outside* the `DataStore` (in `jobservice` and the
   download view), so adding an opaque-reference column achieves nothing until
   those code paths route through the store. **Lead here.**
2. **Q2 ("where does PanDDA2 run?") gates R6** — it decides whether artifacts in
   the Materia deployment are *ever born as relpaths*, which scopes how much of
   the local-resolution path the R6 refactor must preserve. **Answer this before
   writing R6 code.**
3. **Auth is a seam, not a dependency** (R2, corrected). We must *not*
   hard-depend on one host's auth library (`ccp4i2-api`). We name a
   `PANDDA_AUTH_BACKEND` seam with an open `local` default; Materia supplies the
   `ccp4i2` backend out-of-tree.

Sequence: **Q2 → R6 refactor → R0 registry/factories → binding plugins.** The R0
spike (entry-point registry + `get_store()`/`get_runner()` factories +
selectors) is cheap and tempting to do first, but it should follow Q2, because
Q2's answer changes how much local-resolution behaviour the factories' `local`
default must keep working.

---

## Requirement-by-requirement: confirmation vs. work

| R | Materia asks | Status in this repo | Verdict |
|---|---|---|---|
| **R0** | Entry-point plugin seam + settings selector; no deployment glue in-tree | Protocols exist ([storage.py](../inspect_api/storage.py)/[jobs.py](../inspect_api/jobs.py)); **no registry**, `get_runner()` hardcodes `LocalProcessRunner`, **no `get_store()` factory at all** | **Work — but cheap.** Protocols ✅; the registry + factory + `PANDDA_JOB_RUNNER`/`PANDDA_DATA_STORE`/`PANDDA_AUTH_BACKEND` selectors are the missing pieces. |
| **R1** | Relocatable mount prefix (`/api/pandda-inspect/`) + frontend base-URL env | Hardcoded `/api/v1/`; `client/src/api.ts` base is fixed | **Work — small.** One Django include path made configurable + one `NEXT_PUBLIC_*` base-URL env read by the client. |
| **R2** | Auth | Auth is an explicit hole (ROADMAP, "deferred to binding") | **Work — corrected.** Make it a *backend seam* with an open `local` default. **Must not hard-require `ccp4i2-api`** (that would violate R0). See "Auth is a seam" below. |
| **R3** | Stamp `inspected_by` from authenticated user when auth on | `Event.inspected_by` is client free-text today | **Work — small,** and falls out of R2 (needs `request.user`). Keep client-supplied as the DEBUG/`local` fallback. |
| **R4** | Widen `DataStore`: `url_for(ref) -> URL` (302 redirect) + non-path ref | `DataStore` is `open()->bytes` + `exists()` only | **Work — protocol widening.** Add `url_for`; `local` store leaves it unimplemented and falls back to byte-streaming. *Free for reads, not for runs* — see the run-path caveat. |
| **R5** | `JobRunner` submit/poll/cancel without subprocess assumptions; opaque handle; foreign-ref output | `JobSpec` is "what, never where/how" ([jobs.py:34-43](../inspect_api/jobs.py#L34-L43)); handle is an opaque string; `Job.runner_handle` is a generic CharField | **Mostly confirmation.** The seam already supports a CCP4i2 job id as a handle and a remote runner. Output-as-foreign-ref ties into R6. *Run-path caveat applies.* |
| **R6** | Artifact/Event reference is opaque to the core (CCP4i2 uuid, not bare relpath) | `Artifact` = `relpath` + optional embedded `contents`; **`jobservice._resolve_path()` and the download view resolve `source_root`/`relpath` themselves, bypassing `DataStore`** | **Work — the load-bearing refactor.** A uuid column is necessary but insufficient: route *all* resolution through `DataStore` first. **Lead here. Gated by Q2.** |

---

## R6 — the one with teeth (lead here)

The Materia doc's original R6 said "model the reference as opaque to the core."
True, but the repo review showed it's not sufficient. The leak:

- `inspect_api/jobservice.py` has its own `_resolve_path(artifact)` that computes
  `source_root / relpath` and hits the filesystem directly — it does **not** go
  through `DataStore`.
- The artifact download view resolves the same way (relpath under `source_root`,
  lexical traversal guard, then symlink follow — the deliberate behaviour
  documented in CLAUDE.md "Artifact serving / in-place ingest").
- `buildservice.py` lands built ligand bytes on the same filesystem assumption.

So if you add an opaque `(project_uuid, file_uuid)` reference to `Artifact`
today, every one of those paths would still re-interpret it as a filesystem
path and break. **The real R6 is: stop resolving paths outside the store.**
Route `jobservice`, `buildservice`, and the download view through `DataStore`
(`open`/`exists`/`url_for`), so the opaque ref is genuinely honoured and the
filesystem assumption that the `local` default quietly bakes in is removed.

This is why R6 is *the* change, and why it's gated by Q2.

### The run-path caveat (R4/R5 are free for reads, not for runs)

R4's `url_for` 302-redirect makes the **download** path cheap: large
event-maps/MTZ never stream through Django; the store hands back a CCP4i2 (or
SAS) URL and the view redirects. But the **refinement runner is not a
pass-through.** `LocalProcessRunner._build_argv` ([jobs.py](../inspect_api/jobs.py))
feeds servalcat/refmac **local absolute paths**. A CCP4i2-backed runner given
remote uuid inputs must **materialise bytes to local disk** (via the store's
`open`) before the tool can read them, and stage outputs back. Whoever builds
the binding's runner must plan that local-staging step — do not design it as a
pure pass-through. (This caveat is mirrored in the Materia doc's R4.)

---

## Q2 — answer before R6 (the gate)

> **Where does PanDDA2 itself run in Materia?** Is `pandda2.analyse` a CCP4i2
> task/pipeline (one job graph), or an external step whose output is imported?

This is not a footnote; it gates R6. Consequence specific to *this* repo:

`ingest_pandda2` is currently the **entire import boundary** and carries our
hard-won invariants — the apo-start-model design (crystal START = the apo
`-pandda-input.pdb`, every event a candidate pose merged onto it), the
`_replace_imported_dataset_artifacts` GOTCHA (a new event-scoped imported kind
must be added to its `.exclude(...)` or poses get silently nuked), and the
BDC-corrected event-map handling. See [CLAUDE.md](../CLAUDE.md) "Ingest:
PanDDA1 vs PanDDA2" and the [apo-start-model memory].

**If PanDDA2 becomes a CCP4i2 job and events come straight from its report, that
reader may not run in Materia at all** — so which of those invariants are even
*reachable* in that deployment changes. And it decides the born-as-what
question:

- **Born as relpaths** (PanDDA2 runs externally, output imported via
  `ingest_pandda2`) → the `local` resolution path still participates in Materia;
  the R6 refactor must keep it working alongside the uuid path.
- **Born as CCP4i2 uuids** (PanDDA2 is a CCP4i2 job; events from its report) →
  the store is effectively uuid-only in Materia; the R6 refactor can lean harder
  on the foreign-ref path and `ingest_pandda2`'s role shrinks or vanishes there.

Answer Q2 first; it scopes how much local behaviour R6 must preserve.

---

## Auth is a seam, not a dependency (R2, ratified from our side)

The Materia doc's original R2 said "depend on `ccp4i2-api>=0.3` for the auth
middleware." That **violates R0** — a hard dependency on *one host's* auth
library is exactly the deployment-specific coupling R0 forbids. The Materia doc
has been corrected. **Ratifying it here from the repo side, since this is our
tree's rule to keep:**

- Auth is a **third pluggable seam**, peer to `DataStore`/`JobRunner`: a
  `PANDDA_AUTH_BACKEND` selector (`"local" | "ccp4i2" | ...`) with an **open
  `local` default** so standalone/desktop dev stays open.
- The base `pandda-inspect-api` package **does not hard-require `ccp4i2-api`.**
  Materia's `ccp4i2` auth backend (which *does* wire `ccp4i2-api`'s three
  fail-closed middleware) lives in the **Materia-side binding**, registered via
  the same entry-point mechanism as the store/runner plugins.
- Acceptable fallback if a full auth seam proves over-engineered: ship
  `ccp4i2-api` as an **optional extra** (`pip install pandda-inspect-api[ccp4i2]`)
  — the non-negotiable is that the *base* package never hard-requires it.
- R3 (`inspected_by` from `request.user`) is downstream of this: when an auth
  backend is active, stamp identity from the authenticated user; keep
  client-supplied free-text as the `local`/DEBUG fallback.

**Rule (ours to hold):** this repo owns the *protocol* (the seam); the binding
owns the *implementation* (the plug); the repo never names a specific deployment
— and that now explicitly includes auth.

---

## Sequencing (agreed with the Materia side)

1. **Q2 — answer "where does PanDDA2 run"** with the CCP4 dev team / Materia.
   Decides whether artifacts are born as relpaths or uuids; scopes R6.
2. **R6 refactor — route all path resolution through `DataStore`.** The
   load-bearing change. Removes the filesystem assumption from `jobservice`,
   `buildservice`, and the download view. Add the opaque reference column *after*
   the resolution is centralised, not before.
3. **R0 spike — registry + factories + selectors.** Promote the protocols to
   documented/versioned public API; add the `importlib.metadata` entry-point
   loader for groups `pandda_inspect.data_stores` / `pandda_inspect.job_runners`
   (and auth); add `get_store()` alongside `get_runner()`; wire
   `PANDDA_DATA_STORE` / `PANDDA_JOB_RUNNER` / `PANDDA_AUTH_BACKEND`. Keep the
   `local` defaults so the repo runs standalone. **After Q2**, because Q2 decides
   how much local behaviour the `local` default must retain.
4. **R1 / R3 — mount-prefix config + identity-stamping.** Small; can ride
   alongside.
5. **Binding plugins** (Materia-side, out-of-tree): the `ccp4i2` store/runner/auth
   implementations. Not in this repo, ever.

---

## What never lands in this repo

Per R0, held firm: **no `Ccp4i2*` class, no Azure/Service Bus/SLURM code, no
hard `ccp4i2-api` dependency.** This repo ships the protocols, the entry-point
group *names*, the registry/loader, the `local` defaults, and the settings keys.
Every concrete host binding (CCP4i2, CCP4Cloud, SLURM, AWS Batch) is a peer
package, none privileged in the reference tree. This is the same discipline
CCP4i2 used by keeping Azure-AD auth in a separate `ccp4i2-api` package rather
than in CCP4i2 core — the `DataStore`/`JobRunner`/auth bindings get identical
treatment.

---

## Relationship to existing docs

- **Consumer half:** Materia's `PANDDA_INSPECT_INTEGRATION.md` (the R-list, the
  composition, the binding-package home decision).
- **The seams themselves:** [storage.py](../inspect_api/storage.py) (`DataStore`),
  [../inspect_api/jobs.py](../inspect_api/jobs.py) (`JobRunner`/`JobSpec`),
  [DESIGN-artifacts-and-jobs.md](DESIGN-artifacts-and-jobs.md) (design of record
  for the artifact/job model the bindings produce into).
- **The invariants Q2 jeopardises:** [CLAUDE.md](../CLAUDE.md) ("Ingest: PanDDA1
  vs PanDDA2", "Artifact serving / in-place ingest") and the apo-start-model
  design note.
- **Backend-fit posture:** [ROADMAP.md](ROADMAP.md) "Backend-fit studies" already
  lists CCP4i2 and CCP4Cloud as equal-footing candidates to implement the
  `DataStore`/`JobRunner` contract — this note is the concrete first instance.
