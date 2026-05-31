# CLAUDE.md — working notes for pandda-inspect-api

Engineering knowledge for working in this repo. For the *why* (the contract-first
architecture argument), read [README.md](README.md) and
[docs/RATIONALE.md](docs/RATIONALE.md) — this file is the *how*: the non-obvious
gotchas that cost real debugging time.

## Layout

- Django + DRF backend (`inspect_api/`, `config/`), SQLite, OpenAPI at `/api/docs/`.
- React + Moorhen client in `client/` (Vite dev server on :5173, proxies the API).
- Durable artifacts = the relational schema (`Dataset`/`Event`/`Artifact`/`Shell`)
  and the OpenAPI contract. The ingest is a replaceable import boundary.

## Ingest: PanDDA1 vs PanDDA2 are two different readers

The filesystem is an **import boundary**; two management commands read the two
PanDDA output formats into the *same* relational model:

- `ingest_pandda` — **PanDDA1**: parses `pandda/results.json` (+ optional
  `Projects.csv`). JSON-shaped.
- `ingest_pandda2` — **PanDDA2**: PanDDA2 **never writes results.json** (verified
  against the pandda2 source). It emits `analyses/pandda_analyse_events.csv` +
  `pandda_analyse_sites.csv` (global tables, written at END of run) and
  per-dataset `processed_datasets/<dtag>/` dirs (`events.yaml`,
  `<dtag>-z_map.native.ccp4`, `<dtag>-event_N_1-BDC_<x>_map.native.ccp4`,
  `<dtag>-pandda-input.pdb/.mtz`, `ligand_files/`).

PanDDA2 ingest facts that bit us (all real, from a BAZ2B run):
- Event identity = `(dtag, event_idx)`. Dataset-level metrics (resolution,
  R-factors, map uncertainty) live on **every event row**, not a separate record
  — lift them onto the Dataset at ingest.
- `hit_in_site_probability` = PanDDA2's ML score → our `Event.score`;
  `interesting` = PanDDA2's own boolean → `Event.interesting`. Both are the
  *machine's* opinion, kept DISTINCT from the mutable human `Event.decision`.
- `pandda_analyse_sites.csv` centroids are often `(0,0,0)` — **derive site
  centroids from member-event coords**, don't trust the CSV column.
- A processed dataset can have **zero events** (no `events.yaml`); the reader
  must tolerate it. CSV event count can be off-by-one vs event-map files.
- An event may have **multiple** event-map files (one per BDC variant); the CSV
  `1-BDC` token picks the canonical one (`...event_N_1-BDC_<token>_map...`).
- `-pandda-input.pdb/.mtz` are **symlinks** into a sibling `data/` dir.
- **Per-event autobuild lives in `events.yaml`**, keyed by 1-based index ==
  CSV `event_idx`. Each event's `Build:` block names the chosen ligand pose
  (`Build Path`, an **absolute** path into `autobuild/N_M_ligand_0.pdb` —
  relativise to `source_root` via `Path(p).resolve().relative_to(root)`) plus
  `Build Score` / `RSCC` / `Optimal Contour`. We ingest the pose as an
  event-scoped **`LIGAND_POSE`** artifact (NOT a model — it's ligand-only
  coords; the model of record is the per-crystal `Dataset.current_model`) and
  lift the three scores onto `Event.{build_score,rscc,optimal_contour}`. The
  frontend seeds the contour slider from `optimal_contour` and badges built
  event-chips.
  - **GOTCHA (cost real time):** `_reconcile_events` creates the pose, but
    `_replace_imported_dataset_artifacts` runs *after* and deletes every
    imported dataset artifact NOT in its `.exclude(kind__in=...)` list — a new
    event-scoped imported kind MUST be added there or it's silently nuked
    (poses read 309-created-then-0-in-DB). Also: stale `inspect_api/__pycache__`
    can mask `reconcile.py` edits; clear it if counts look wrong.

Re-running an ingest **clobbers** decision state (replace, not reconcile). The
reconciliation policy for re-ingest / PanDDA-rerun is an open design question.

## Artifact serving / in-place ingest

- `Artifact.relpath` is resolved against **`project.source_root`** (the tree it
  was ingested from), falling back to `PANDDA_DATA_ROOT/<name>`. This lets you
  ingest a large/externally-licensed dataset **in place** (`ingest_pandda2
  --root /anywhere`) without copying it into the repo tree.
- The download view's traversal guard checks the relpath **lexically (normpath)
  BEFORE resolving symlinks**, then follows the symlink. This is deliberate: it
  blocks `../` escapes while still serving PanDDA2's symlinked inputs (whose
  targets legitimately live outside `source_root`).
- A web client can NEVER hand the server a directory path (browser sandbox) — so
  "ingest without copy" is a CLI / Electron / register-path affordance, not a
  browser one. `source_root` is the single abstraction that expresses all three.

## Moorhen integration — THE big lesson: Moorhen is Redux-driven

The client embeds Moorhen 0.23 (real source for reference:
`~/Developer/emsdk/Moorhen/baby-gru/src`). The hardest-won, most reusable lesson:

> **Camera origin, map contour level, and map registration are all driven by the
> Redux store. Mutating the imperative `glRef.current.*` / `map.*` properties
> moves nothing reactive — it silently no-ops. ALWAYS dispatch the action.**

Concretely, in `client/src/components/InspectDrawer.tsx` (event viewer) and the
typed wrappers in `client/src/moorhen-shim.ts`:

- **Recentre the view** → `dispatch(setOrigin([-x,-y,-z]))` (store holds the
  NEGATED look-at point). `MoorhenMap.drawMapContour` reads `glRef.origin` from
  the store, so the dispatch is what makes the map re-contour at the new centre
  AND follow on pan. (We also nudge `glRef.current.setOrigin` because this
  build's store→GL sync `useEffect` is commented out.)
- **Change contour level** → `dispatch(setContourLevel({ molNo, contourLevel }))`.
  `MoorhenMapManager` re-contours off the `mapContourSettings.contourLevels`
  slice. Setting `map.contourLevel` + `map.drawMapContour()` does NOT re-render.
- `setActiveMap` only sets the refinement-target map — NOT centre-tracking.
- `drawMapContour()` is for contour-LEVEL changes only, not camera moves.

### Event-map specifics (PanDDA event maps are direct-read CCP4, not MTZ)

- They are `.ccp4` real-space maps → load with `loadToCootFromMapURL`, NOT
  `loadToCootFromMtzURL` (which expects FEVENT/PHEVENT columns and fails with
  "CCP4MTZfile open_read File missing or corrupted"). Branch on file extension;
  keep the MTZ path for PanDDA1.
- **`isEM` / `isOriginLocked` trap:** a directly-read CCP4 map runs `is_EM_map`
  on load; a PanDDA box can trip it → `isOriginLocked=true` → `doCootContour`
  OVERRIDES the passed origin with the cell centre, pinning density to a fixed
  spot regardless of `setOrigin`. **Fix: set `map.isEM=false;
  map.isOriginLocked=false` after load.** (MTZ maps never hit this path — that's
  why it only affects event maps.)
- **Contour units:** Coot's contour API is in **ABSOLUTE map units**, not σ.
  Convert: `level = sigma * map.mapRmsd` (Moorhen's own default logic does this).
  A bare `1.0` absolute gives an arbitrary level for any map whose RMSD isn't ~1.
- **Default level:** event maps are **BDC-corrected** (bound-state density
  restored toward full occupancy) → viewed like a normal 2Fo-Fc map (single
  positive contour, `isDifference=false`), NOT an Fo-Fc difference map at ±3σ.
  Default `DEFAULT_EVENT_SIGMA = 2.0` (BDC inflates contrast; 1σ is too bulky).
  Ideal level is dataset/event-dependent — the slider retunes.
- Load order: **recentre BEFORE loading the map** so its first contour lands on
  the event.

## Conventions

- Client lint: 79-col, explicit `encoding=` on `open()`, typed shims over
  Moorhen's loose alpha `.d.ts` (don't fight the alpha types — wrap in
  `moorhen-shim.ts`). Run `npx tsc --noEmit -p tsconfig.json` in `client/`.
- Public sample data is BAZ2B (Zenodo DOI 10.5281/zenodo.48768, CC-BY-SA) —
  fetched OUTSIDE the repo at `~/Developer/pandda-data/`. ShareAlike ⇒ do NOT
  vendor it into the repo; reference by DOI + fetch script.
