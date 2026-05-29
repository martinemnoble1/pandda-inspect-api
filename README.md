# pandda-inspect-api

A **thin reference implementation** of a contract-first, API-based backend for
`pandda.inspect`. It exists to demonstrate one principle:

> pandda.inspect should be a **server–API–client** system, where the API is a
> versioned, OpenAPI-specified **contract**, and the client (Moorhen, Coot,
> CLI, …) talks only to that contract.

It is deliberately minimal. The point is the **contract and the data model**,
not this particular backend — which any platform (including CCP4i2 / CCP4Cloud)
could implement instead.

## The key design decision

PanDDA emits a **filesystem tree** plus `results.json` / CSV sidecars. That is
fine as *output*, but it is not a sound *source of truth* for an interactive,
multi-user, concurrently-refined inspection tool: there is no atomicity, no
constraint enforcement, and "decision state" (is this event a hit? who said so?
has it been refined?) has nowhere coherent to live.

So this reference does the obvious thing:

```
PanDDA filesystem  ──(ingest, once)──►  SQL (transactional)  ──►  REST API  ──►  client
   results.json / CSV                    Dataset / Event /         OpenAPI
   (read-only input adapter)            Artifact / Shell          contract
```

- **Big immutable artifacts** (maps, MTZ, model coords) stay on disk / a blob
  store and are *referenced* by the DB — streamed via the API, never copied
  into it.
- **Small mutable decision/provenance state** (event `decision`, `confidence`,
  `inspected_by`, timestamps) lives in the database, where transactions and
  constraints keep it coherent under concurrent access.

The filesystem becomes an **import boundary**, not the source of truth.

## Pluggable seams (shown, not yet built out)

Two interfaces mark where deployment-specific behaviour plugs in, so the same
codebase can target laptop / lab cluster / cloud:

- `storage.DataStore` — where artifacts live (local FS now; S3 / Azure Blob /
  a CCP4Cloud store later).
- `jobs.JobRunner` — how compute is launched (local detached process now;
  qsub / SLURM / Azure Batch / a CCP4Cloud executor later).

Each has one trivial implementation here. They are the integration points; the
contract above them does not change when the implementation behind them does.

## Quickstart

```bash
cd ~/Developer/pandda-inspect-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py ingest_pandda \
  --project Pandda-CDK2A-RXL \
  --root ~/Developer/MoorhenPanddaApp/PanddaProjects/Pandda-CDK2A-RXL

python manage.py runserver 8000
```

Then:

- **OpenAPI schema:** http://localhost:8000/api/schema/
- **Swagger UI (browse the contract):** http://localhost:8000/api/docs/
- **Browsable API:** http://localhost:8000/api/v1/datasets/ , `/events/` , `/artifacts/`
- Record a decision: `PATCH /api/v1/events/{id}/` with `{"decision": "hit"}`

## What this is NOT

- Not production code, not authn/authz, not the job-execution path (stubbed).
- Not a proposal to replace CCP4Cloud — the contract is what any backend,
  CCP4Cloud included, could serve.
