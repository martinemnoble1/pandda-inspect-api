# pandda.inspect: the case for a contract-first API

*A one-page rationale to accompany the reference implementation in this repo.*

## The claim

`pandda.inspect` should be a **server–API–client** system, in which the API is
a **versioned, OpenAPI-specified contract**. Everything in front of that
contract is one of several possible clients (Moorhen web, Coot, CCP4i2, a CLI);
everything behind it is a swappable implementation. Get the contract right and
the deployment question — laptop, lab cluster, or cloud — becomes a
configuration choice rather than a rewrite.

This repo is a deliberately **thin reference** that demonstrates the principle
end-to-end against real PanDDA output. It is not a product, and it is not a
proposal about *whose* backend should ultimately serve the contract — only that
there should *be* one.

## The problem it addresses

PanDDA emits a **filesystem tree** plus `results.json` / CSV sidecars. That is
an excellent *output format*. It is a poor *source of truth* for an
interactive, multi-user tool where datasets are processed and refined
concurrently:

- **No atomicity.** Recording an inspection decision means rewriting a JSON
  blob; two concurrent writers race, and one silently wins.
- **No invariants.** Nothing enforces "an event belongs to a dataset that
  exists", or "a model can't be marked refined before its job completed".
- **No coherent home for decision state.** "Is this event a hit? who said so?
  when? has it been refined?" is *mutable human judgement*, and it has nowhere
  to live except more files alongside the immutable scientific output.

These are not hypothetical: they are exactly the failure modes a shared,
long-running inspection service hits first.

## The shape of the fix

Separate the two kinds of state, and give each the store it actually needs:

```
PanDDA filesystem  ──(ingest, once)──►  SQL (transactional)  ──►  REST API  ──►  client
   results.json / CSV                    Dataset / Event /         OpenAPI
   (read-only input adapter)            Artifact / Shell          contract
```

- **Big immutable scientific artifacts** (coords, MTZ, event/Z maps, ligand
  dicts) stay on disk / a blob store and are *referenced* by the database —
  streamed through the API, never copied into it.
- **Small mutable decision / provenance state** (event `decision`,
  `confidence`, `inspected_by`, timestamps) lives in a transactional database,
  where constraints and row-level locking keep it coherent under concurrent
  access.

The filesystem becomes an **import boundary**, not the source of truth. This
repo's `ingest_pandda` management command is that boundary; after it runs, the
API serves SQL.

> In this reference the database is SQLite and the ingest is one command — the
> humblest possible implementation. The durable artifacts are the **relational
> schema** and the **OpenAPI contract**, not the storage engine.

## Two pluggable seams

The same codebase targets very different deployments because two interfaces
absorb the variation. They are present in the code (`storage.DataStore`,
`jobs.JobRunner`) with one trivial implementation each, and marked as the
integration points:

| Seam | Laptop | Lab | Cloud |
|------|--------|-----|-------|
| **DataStore** (artifact bytes) | Local FS | shared / NFS | S3 / Azure Blob |
| **JobRunner** (compute launch) | detached process | qsub / SLURM | Azure Batch / AWS Batch |

A `JobSpec` describes *what* to compute, never *where* — so no paths or
scheduler flags leak across the contract. The API never knows how a job runs;
it hands a spec to a runner and polls a handle.

**This is the vendor-neutral point:** the contract above these seams does not
change when the implementation behind them does. A platform such as CCP4i2 /
CCP4Cloud could implement either seam — or serve the whole contract — without
any client noticing. The architecture invites that integration rather than
competing with it.

## What the reference proves (against real data)

Ingesting the CDK2A-RXL sample yields 23 datasets, 8 events, 78 artifacts, 7
shells, and:

- a browsable **OpenAPI contract** at `/api/docs/`;
- **artifact streaming** — a 25 MB MTZ and event maps served over HTTP from the
  DataStore, by reference;
- an **atomic, constraint-backed decision write**:
  `PATCH /api/v1/events/{id}/ {"decision": "hit"}` persists and stamps
  `inspected_at` — the operation the filesystem model cannot do safely.

Notably, PanDDA's event model (N events per dataset, each with metrics *and* a
mutable human decision) maps onto a small relational schema
(`Dataset`/`Event`/`Artifact`/`Shell`) **without strain** — useful evidence,
on neutral ground, that this data is a natural fit for a transactional backend.

## What this is deliberately *not*

- Not production code: no authentication/authorization (a deployment concern
  layered on later, not part of the contract demo).
- Not the job-execution path: `JobRunner` is stubbed; the point here is the
  read/decision contract, not running PanDDA.
- Not a backend-vendor proposal. The contract is the deliverable; who serves it
  is a separate, later conversation.
