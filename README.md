# Reinspect (pandda-inspect-api)

**Reinspect** is a desktop app for reviewing [PanDDA](https://pandda.bitbucket.io/)
events — browsing analyses, triaging events, and inspecting electron density in
[Moorhen](https://moorhen.org) — backed by a contract-first REST API.

> An independent prototype reimagining the `pandda.inspect` event-review
> workflow. **Not affiliated with or endorsed by the PanDDA project.**

It is also a **reference implementation** of one architectural idea: that
pandda.inspect should be a **server–API–client** system, where the API is a
versioned, OpenAPI-specified **contract** and the client talks only to that
contract. The same backend powers the laptop (Electron) binding here and could
power a lab-cluster or cloud deployment unchanged.

---

## Who you are

| You want to… | Go to |
|---|---|
| **Just use it** — review PanDDA events on your machine | [1. Run the desktop app](#1-run-the-desktop-app) |
| **Hack on it** — backend + client from source | [2. Develop from source](#2-develop-from-source) |
| **Understand the design** — why API-first, what the seams are | [3. Architecture](#3-architecture) |

---

## 1. Run the desktop app

The simplest way. Download a signed installer from the
**[Releases page](../../releases/latest)** — no Python, Node, or build step:

- **macOS** (Apple-silicon): `Reinspect-<ver>-mac-arm64.dmg` — **signed +
  notarized**, double-clicks straight open.
- **Windows**: `Reinspect-<ver>-win-x64.exe` — unsigned; SmartScreen →
  **More info → Run anyway**.
- **Linux**: `Reinspect-<ver>-linux-x86_64.AppImage` or `…-amd64.deb` — unsigned.

The app bundles its own backend (a frozen Django server) and opens the
inspector in a window. On first run the database is empty — use **Import** to
add a PanDDA dataset:

- **Browse folder (ingest in place)** *(desktop only)* — point it at a PanDDA
  output directory; it's ingested **where it lives, with no copy**.
- **Import a zip** — upload a zipped PanDDA output / crystals directory.

> Need a dataset to try it on? See [Generating test data](#generating-test-data).

You can choose where the app stores data (DB, refinement outputs, imported
copies) under **Settings → Data folder**.

---

## 2. Develop from source

**Prerequisites:** Python 3.12+ (developed on 3.14) · Node 20+ (developed on 24).
Full, audience-specific setup is in **[docs/SETUP.md](docs/SETUP.md)**; the
short version:

```bash
# backend (Django + SQLite, OpenAPI at /api/docs/)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit paths — see docs/SETUP.md
python manage.py migrate
python manage.py runserver 8000

# client (React + Moorhen, in a second terminal)
cd client && npm install && npm run dev   # Vite on :5173, proxies /api -> :8000
```

Then open the Swagger UI at <http://localhost:8000/api/docs/>, or the client at
<http://localhost:5173>. The inspect + decision loop and the **test suite** need
**no** CCP4/PanDDA2 install (`python manage.py test`); dispatching a *refinement*
does — see [docs/SETUP.md §B](docs/SETUP.md).

To build the desktop app yourself (freeze the backend + package with
electron-builder), see **[packaging/README.md](packaging/README.md)**.

### Generating test data

PanDDA's public reference dataset is **BAZ2B** (Zenodo DOI
[10.5281/zenodo.48768](https://doi.org/10.5281/zenodo.48768), CC-BY-SA — fetch
it outside the repo; do **not** commit it). The Zenodo bundle is a *curated
results* tree, **not** a PanDDA output directory Reinspect can read directly, so
to get an ingestable dataset you **run PanDDA2 over it yourself**. PanDDA2 is a
separate tool — [github.com/xchem/pandda_2_gemmi](https://github.com/xchem/pandda_2_gemmi)
(install + run per that repo; out of scope here). Running it on the canonical
BAZ2B dataset works cleanly (incl. on an M1 Mac) and produces a `pandda2_out/`
directory, which is a valid PanDDA2 output root Reinspect ingests:

```bash
# in the desktop app: Import → Browse folder → select  …/pandda2_out
# from source (CLI), the same thing without a copy:
python manage.py ingest_pandda2 \
  --project BAZ2B \
  --root ~/pandda-data/BAZ2B-zenodo-48768/pandda2_out
```

Re-running ingest is **safe** — additive and import-scoped; it refreshes
imported artifacts and machine metrics but never clobbers human decisions or
built/refined models.

---

## 3. Architecture

PanDDA emits a **filesystem tree** plus `results.json` / CSV sidecars. That is
fine as *output*, but it is not a sound *source of truth* for an interactive,
concurrently-refined inspection tool: no atomicity, no constraint enforcement,
and "decision state" (is this event a hit? who said so? has it been refined?)
has nowhere coherent to live. So Reinspect does the obvious thing:

```
PanDDA filesystem  ──(ingest, once)──►  SQL (transactional)  ──►  REST API  ──►  client
   results.json / CSV                    Dataset / Event /         OpenAPI
   (read-only input adapter)            Artifact / Shell          contract
```

- **Big immutable artifacts** (maps, MTZ, model coords) stay on disk / a blob
  store and are *referenced* by the DB — streamed via the API, never copied in.
- **Small mutable decision/provenance state** (event `decision`, `confidence`,
  `inspected_by`, timestamps) lives in the database, where transactions keep it
  coherent under concurrent access.

The filesystem becomes an **import boundary**, not the source of truth.

### Pluggable seams

Two interfaces mark where deployment-specific behaviour plugs in, so the same
codebase targets laptop / lab cluster / cloud:

- `storage.DataStore` — where artifacts live (local FS now; S3 / Azure Blob /
  a CCP4Cloud store later).
- `jobs.JobRunner` — how compute is launched (local detached process now;
  qsub / SLURM / Azure Batch / a CCP4Cloud executor later).

The contract above them does not change when the implementation behind them
does. Deeper rationale: **[docs/RATIONALE.md](docs/RATIONALE.md)** and
**[docs/DESIGN-artifacts-and-jobs.md](docs/DESIGN-artifacts-and-jobs.md)**;
working notes for the codebase are in **[CLAUDE.md](CLAUDE.md)**.

## What this is NOT

- Not production code, not authn/authz.
- Not the official pandda.inspect, and not affiliated with the PanDDA project.
- Not a proposal to replace CCP4Cloud — the **contract** is what any backend,
  CCP4Cloud included, could serve.
