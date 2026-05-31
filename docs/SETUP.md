# Setup & onboarding — pandda-inspect-api

How to run this reference, for two audiences:

- **[A. Developer](#a-developer)** — work on the app, run it, run the tests.
  **No CCP4 / PanDDA2 install needed**: the inspect + decision loop and the
  whole test suite run without them.
- **[B. Tester](#b-tester-full-loop-incl-refinement)** — exercise the *full*
  loop including dispatching a `giant.quick_refine` refinement. This needs CCP4
  **and** a PanDDA2 conda environment, activated in a specific order (§B.2).

> A third audience — **end users** running a packaged desktop app — is the goal
> of the Electron binding (ROADMAP #6 / DESIGN §3.1). Until that ships, a "user"
> is a tester. This doc will grow a User section when the installer exists.

Companion docs: [README](../README.md) (why), [CLAUDE.md](../CLAUDE.md)
(engineering *how* / gotchas), [DESIGN](DESIGN-artifacts-and-jobs.md) (the
artifact/job/binding design), [ROADMAP](ROADMAP.md) (what's next).

---

## A. Developer

### A.1 Prerequisites

- Python 3.12+ (developed on 3.14)
- Node 20+ (developed on 24) for the client

### A.2 Backend

```bash
cd pandda-inspect-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then edit paths — see "Configuration" below
python manage.py migrate
python manage.py runserver 8000
```

- OpenAPI schema: http://localhost:8000/api/schema/
- Swagger UI: http://localhost:8000/api/docs/
- Browsable API: http://localhost:8000/api/v1/datasets/ , `/events/`, `/artifacts/`

### A.3 Client

```bash
cd client
npm install
npm run dev                   # Vite on :5173, proxies /api -> :8000
```

The client talks **only** to the REST contract; in dev Vite proxies `/api` to
the backend, so they share an origin (which is also how the packaged bindings
serve it — the client needs no runtime API-URL config).

### A.4 Sample data (public BAZ2B)

The repo vendors **no** PanDDA data (the public set is ShareAlike-licensed; see
below). Fetch it outside the repo and ingest by reference:

1. **Download BAZ2B** — BAZ2B vs the Zenobia fragment library, Zenodo DOI
   **[10.5281/zenodo.48768](https://doi.org/10.5281/zenodo.48768)**,
   **CC-BY-SA-4.0**, 201 datasets. Unpack to a location **outside** this repo,
   e.g. `~/pandda-data/BAZ2B-zenodo-48768/`. ShareAlike ⇒ **do not** commit it
   into git.

2. **You need PanDDA *output*, not raw data.** Zenodo ships the raw
   fragment-screening data. To get a `processed_datasets/` + `analyses/` tree to
   ingest you must **run PanDDA on it** (PanDDA2 — needs the same environment as
   §B). If a colleague has already produced a `pandda2_out/` tree, point at that.

3. **Ingest** (PanDDA2 reader; for PanDDA1 `results.json` trees use
   `ingest_pandda`):

   ```bash
   python manage.py ingest_pandda2 \
     --project BAZ2B \
     --root ~/pandda-data/BAZ2B-zenodo-48768/pandda2_out
   ```

   Re-running is **safe**: ingest is additive and import-scoped — it refreshes
   imported artifacts + machine metrics but never clobbers human decisions or
   built/refined models (DESIGN §1.3).

### A.5 Tests

```bash
python manage.py test inspect_api      # hermetic; no CCP4/PanDDA2 needed
python manage.py check
```

---

## B. Tester (full loop, incl. refinement)

Everything in A, plus the ability to dispatch a refinement of the current-best
model and watch it become the new `current_model`. The runner drives **stock
CCP4** refinement (`servalcat` by default, `refmac5` as a fallback) directly —
**not** `giant.quick_refine`. Why: `giant.quick_refine` is only a thin wrapper
around REFMAC, may be locally patched in a given install (non-reproducible), and
the tool we actually want is plain CCP4. See DESIGN §5.8.

> Note: an earlier plan assumed a PanDDA2 conda `giant.quick_refine`. Inspecting
> a real install showed (a) it's just a REFMAC launcher and (b) CCP4 also ships
> an older PanDDA1 `giant.refine` — a name-clash footgun. Driving `servalcat`
> from stock CCP4 sidesteps both and needs **no conda env** for refinement.

### B.1 What you need

Just **CCP4 set up** (for `servalcat`). The conda-env variables below are
optional — only needed if you point `REFINE_TOOL` at a conda-only tool.

### B.2 Wiring (env vars in `.env`)

```
CCP4_SETUP_SH=/Applications/ccp4-9/bin/ccp4.setup-sh   # required for refinement
# Optional — only if REFINE_TOOL lives in a conda env (servalcat does NOT):
CONDA_SH=/Users/you/miniconda3/etc/profile.d/conda.sh
PANDDA2_CONDA_ENV=pandda2
# Optional — override the engine (default: servalcat; e.g. refmac5):
# REFINE_TOOL=refmac5
```

You do **not** run activation by hand for the app — the backend sources
`CCP4_SETUP_SH` (then the conda env if set) in a wrapper before each refinement.
This is the **only** host-specific bit; it lives outside the API and the job
spec (the `JobRunner` seam absorbs "where/how"; the contract carries only
"what"). A different machine sets different values; nothing else changes.

### B.3 Verify the environment is wired

The runner dry-run-probes the activation at submit time and gates dispatch
(with a clear reason) if it fails. To check manually:

```sh
sh -c '. "$CCP4_SETUP_SH"; command -v servalcat'
```

It should print a path to `servalcat` inside your CCP4 install. If it prints
nothing, CCP4 isn't set up — fix `CCP4_SETUP_SH` in `.env`. (You can also hit
`GET /api/v1/jobs/refine_available/` — it returns the probe result the UI uses
to gate dispatch.)

### B.4 Run a refinement

With the backend running and a project ingested, dispatch via the API:

```sh
# submit a refinement of dataset <id>'s current-best model
curl -s -X POST http://localhost:8000/api/v1/jobs/submit/ \
  -H 'Content-Type: application/json' -d '{"dataset": <id>}'
# poll until it leaves "running" (this also lands the result)
curl -s http://localhost:8000/api/v1/jobs/<job_id>/
```

The job runs `servalcat refine_xtal_norefmac` under a per-job working dir
(`<source_root>/jobs/<job_id>/`); on success the refined model is registered as
an `Artifact(origin=refined)` and becomes the dataset's `current_model`
(verified end-to-end on BAZ2B). A UI to drive this lands with ROADMAP #4b.

---

## Configuration (environment variables)

Config crosses into the backend via env vars (the same mechanism every binding
uses — see DESIGN §5.7). Copy `.env.example` to `.env` and edit. Summary:

| Variable | Purpose | Default |
|---|---|---|
| `PANDDA_DATA_ROOT` | Where ingested project trees live (artifact serving) | `<repo>/data` |
| `PANDDA_DB_PATH` | SQLite file location | `<repo>/db.sqlite3` |
| `PANDDA_JOBS_ROOT` | Fallback root for job working dirs (per-project `source_root` is used when set) | = `PANDDA_DATA_ROOT` |
| `CCP4_SETUP_SH` | Path to CCP4 `ccp4.setup-sh` (tester; required for refinement) | _(unset → refine gated)_ |
| `CONDA_SH` | Path to conda `profile.d/conda.sh` (optional; conda-only tools) | _(unset)_ |
| `PANDDA2_CONDA_ENV` | conda env to activate (optional; servalcat needs none) | _(unset)_ |
| `REFINE_TOOL` | Refinement engine invoked after activation | `servalcat` |

`.env` is git-ignored — it holds *your* machine's paths. Never commit it.

---

## Troubleshooting

- **Swagger UI blank / 500** — ensure `pip install -r requirements.txt` ran in
  the active venv (it needs `drf-spectacular`).
- **Artifact download 404** — the project's `source_root` (set at ingest) must
  still point at the on-disk tree; re-ingest with `--root` if you moved it.
- **`servalcat: command not found` / refine fails** — CCP4 isn't set up; check
  `CCP4_SETUP_SH` in `.env` and re-run the probe in §B.3.
- **Refinement dispatch disabled in the UI** — the activation probe failed;
  verify `.env` per §B.2–B.3.
