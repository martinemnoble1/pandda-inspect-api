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
  — lift them onto the **`RunDataset`** at ingest (they are per-run, not a
  crystal property — multi-run, Phase B; they used to sit on `Dataset`).
- `hit_in_site_probability` = PanDDA2's ML score → our `Event.score`;
  `interesting` = PanDDA2's own boolean → `Event.interesting`. Both are the
  *machine's* opinion, kept DISTINCT from the mutable human decision — which now
  lives on **`Finding`** (run-independent, shared across runs of a site via
  spatial association; `event.decision` is a read-through proxy). Phase C.
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
  event-chips. **UNITS GOTCHA:** `Optimal Contour` is in **ABSOLUTE** map units,
  NOT σ — pandda2 computes it as a threshold on raw BDC-corrected event-map
  sample values (`autobuild/inbuilt.py get_optimal_signal_contour`); BAZ2B range
  ~0.18–9, median ~1.2. Moorhen's stored `contourLevel` is also absolute (it
  seeds `nσ * mapRmsd`; the map-card slider shows `level/mapRmsd` as σ). So pass
  `optimal_contour` to `setContourLevel` **directly** (do NOT `* mapRmsd`); only
  σ-based defaults get `* mapRmsd`. Our own σ-domain slider stores
  `optimal_contour / mapRmsd` so its σ readout/retune stays consistent.
- **Crystal START model = the apo `-pandda-input.pdb`** (ligand-free), set as
  `Dataset.current_model` at ingest — NOT the merged `pandda-model.pdb`. Every
  event is then a *candidate* pose merged onto apo; merges accumulate
  (`current_model` lineage chains apo ← +L1 ← +L2 …). The merged
  `pandda-model.pdb` is still catalogued but only as a machine-opinion
  **reference** artifact (a second `structure` artifact — so anything picking
  "the structure" must disambiguate by the `-pandda-input.pdb` suffix).
  `Event.pose_merged` is null at ingest (nothing pre-merged) and set True only
  by the merge action. Rationale + the bugs this avoids: the apo-start-model
  design note + DESIGN §1.2.
  - **GOTCHA (cost real time):** `_reconcile_events` creates the pose, but
    `_replace_imported_dataset_artifacts` runs *after* and deletes every
    imported dataset artifact NOT in its `.exclude(kind__in=...)` list — a new
    event-scoped imported kind MUST be added there or it's silently nuked
    (poses read 309-created-then-0-in-DB). Also: stale `inspect_api/__pycache__`
    can mask `reconcile.py` edits; clear it if counts look wrong.

Re-running an ingest **clobbers** decision state (replace, not reconcile). The
reconciliation policy for re-ingest / PanDDA-rerun is an open design question —
a proposed answer (run-scoped Observations + run-independent Findings, for the
*different-run* case) is in
[docs/MULTI_RUN_DATA_MODEL.md](docs/MULTI_RUN_DATA_MODEL.md). NB the merge key
`(dtag, event_num)` is an unstable per-run ordinal: across two runs A `event 1`
can be the same blob as B `event 2` (verified). To match events across runs use
the detection-cluster centroid (from `events.yaml` `Position Array` voxels), NOT
`xyz_centroid` (build-contaminated == Build centroid) or autobuild poses (they
wander ~20Å across runs).

## Artifact serving / in-place ingest

- `Artifact.relpath` is resolved against **`project.source_root`** (the tree it
  was ingested from), falling back to `PANDDA_DATA_ROOT/<name>`. This lets you
  ingest a large/externally-licensed dataset **in place** (`ingest_pandda2
  --root /anywhere`) without copying it into the repo tree.
- The traversal guard checks the relpath **lexically (normpath) BEFORE resolving
  symlinks**, then follows the symlink. This is deliberate: it blocks `../`
  escapes while still serving PanDDA2's symlinked inputs (whose targets
  legitimately live outside `source_root`). This guard now lives in
  `storage.LocalFileStore._resolve` (the download view routes through
  `storage.get_store(project).local_path(relpath)`), NOT inline in the view.
- **READ paths go through the `DataStore` seam (`storage.get_store`)** — the
  single place that turns an artifact ref into bytes/a file, so a non-local
  store (object storage, CCP4i2 uuids) slots in without touching callers
  (Materia R6). **Do NOT add another inline `source_root`/`relpath` resolver.**
  The one remaining local resolver is `jobservice._resolve_path` (the RUN path:
  it also builds paths for not-yet-written outputs, and a non-local store must
  *materialise* bytes locally for servalcat/refmac — not a pass-through). That
  staging is deferred, gated by Q2 — see docs/MATERIA_INTEGRATION.md.
- A web client can NEVER hand the server a directory path (browser sandbox) — so
  "ingest without copy" is a CLI / Electron / register-path affordance, not a
  browser one. `source_root` is the single abstraction that expresses all three.
- **Deletion/cleanup mirrors this ownership story** (proposed, not yet built —
  [docs/DELETION_AND_CLEANUP.md](docs/DELETION_AND_CLEANUP.md)). The DB cascades
  are wired; the *files* are the design. Cleanup is driven off **`Artifact.origin`**
  (delete bytes only for `BUILT`/`REFINED` — never IMPORTED in-place trees, which
  are the user's) plus **`Run.out_dir` provenance** for analysis output trees,
  via a new **`DataStore.delete()`** on the seam (do NOT add an inline resolver).
  Note the **submit-time zombie guard**: deleting a project cascades the `Run`
  rows but leaves the `out_dir` on disk, and `runservice` does
  `mkdir(exist_ok=True)` with DB-resident idempotency — so a fresh analysis
  silently writes into the stale tree. The invariant: *a populated `out_dir` is
  owned by exactly one live `Run` (or nobody)*; refuse an unowned populated dir.

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
- **Contour units — event maps are ABSOLUTE, model maps are σ.** Coot's contour
  API always wants absolute map units. For model maps (full-cell X-ray), σ is
  meaningful, so convert `level = sigma * map.mapRmsd` (Moorhen's own default
  logic). For **event maps, DO NOT use σ at all**: the BDC-corrected box is
  mostly flat-zero outside the event, so the whole-box `mapRmsd` is tiny (~0.13
  for BAZ2B) and `σ = level/RMSD` blows up to a meaningless ~18 (the symptom: a
  σ slider pinned at its rail). The native scale IS absolute — pandda2's
  "Optimal Contour" (~2.4), Coot's `suggestedContourLevel` (~0.8) and the
  applied level all sit on it. So `LoadedMap.unit` is `"absolute"` for event
  maps, `"sigma"` for model maps; the slider/label work in that unit and
  `onContour` only `* mapRmsd` for the σ case. See contour-units memory.
- **Default level:** event maps are **BDC-corrected** (bound-state density
  restored toward full occupancy) → viewed like a normal 2Fo-Fc map (single
  positive contour, `isDifference=false`), NOT an Fo-Fc difference map at ±3σ.
  Seed the event contour (absolute) from Coot's `suggestedContourLevel` (≈0.8,
  empirically "about right" for BAZ2B), else optimal_contour, else
  `DEFAULT_EVENT_LEVEL = 2.0`. NB pandda2's optimal_contour (~2.4) is a
  signal-detection threshold, NOT a viewing level — it renders too tight as a
  default. Absolute slider range 0–`EVENT_LEVEL_MAX` (6.0). Model maps default
  `DEFAULT_2FOFC_SIGMA = 1.5` / `DEFAULT_FOFC_SIGMA = 3.0`, σ slider to
  `MAP_SIGMA_MAX` (5σ) / `DIFF_SIGMA_MAX` (8σ).
- **Contour race (cost real time):** `MoorhenMapManager`'s mount `useEffect`
  (`intiliaseMap`) dispatches its OWN default `setContourLevel` for each freshly
  added map (non-EM non-difference → `1*mapRmsd`). It runs AFTER the synchronous
  `setContourLevel` we dispatch post-`addMap`, so it CLOBBERS ours — the map
  renders at Moorhen's default until the slider is first touched, then "jumps"
  to our level. Fix: re-assert our levels in a `setTimeout(…, 0)` after
  `setMaps` (a macrotask runs after React flushes that mount effect, so we win).
  Keep the synchronous dispatch too — it's what works when re-loading into an
  already-mounted manager (no mount effect re-fires).
- Load order: **recentre BEFORE loading the map** so its first contour lands on
  the event.

## Conventions

- Client lint: 79-col, explicit `encoding=` on `open()`, typed shims over
  Moorhen's loose alpha `.d.ts` (don't fight the alpha types — wrap in
  `moorhen-shim.ts`). Run `npx tsc --noEmit -p tsconfig.json` in `client/`.
- Public sample data is BAZ2B (Zenodo DOI 10.5281/zenodo.48768, CC-BY-SA) —
  fetched OUTSIDE the repo at `~/Developer/pandda-data/`. ShareAlike ⇒ do NOT
  vendor it into the repo; reference by DOI + fetch script.
