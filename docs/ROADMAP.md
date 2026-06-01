# pandda-inspect-api — Roadmap & status

Prioritised next steps and where we are against them. Companion to
[README.md](../README.md) (overview), [CLAUDE.md](../CLAUDE.md) (engineering
*how* / gotchas), [docs/RATIONALE.md](RATIONALE.md) (the contract-first *why*),
and [client/PANDDA2_INTEGRATION.md](../client/PANDDA2_INTEGRATION.md)
(Moorhen/PanDDA2 client specifics). This file is the single place for *what's
next and in what order*.

Last updated: 2026-06-01 (main @ `9769842`; reconciled against the codebase by
audit — the build+refine loop and the Electron app shipped since the prior
update, so several items below moved DESIGNED/PACKAGING → DONE).

## Where we are (snapshot)

- **MVP works end to end + the full inspect→build→refine loop is LIVE.** Django
  +DRF backend (Project/Dataset/Event/Artifact/Shell) + React/Moorhen client.
  You can browse, triage, **merge a ligand at its pose (→ auto-decision Hit)**,
  **dispatch a crystal refinement and have the model + map update on
  completion**, all through the UI. Public repo, clean history.
- **Reinspect desktop app SHIPPED** (was ROADMAP #6): signed + notarized
  cross-platform installers (macOS `.dmg`, Windows `.exe`, Linux
  `.AppImage`/`.deb`) on the [Releases page](../../releases/latest), built by
  `.github/workflows/electron.yml`. Current release **v0.1.1**. See
  [packaging/README.md](../packaging/README.md) and the electron-shell /
  electron-ci-signing notes.
- **Ingest-in-place** (no-copy): point the app's *Browse folder* at a PanDDA
  output dir and it's ingested where it lives (`source_root`), via the
  localhost-guarded `POST /api/v1/projects/ingest_path/`
  (`importer.ingest_path`). Not in the old ROADMAP at all.
- **Build + refine artifact paths are DONE** (were #4/#4b "designed"): ligand
  merge → `origin=built` artifact repointing `Event.current_model` +
  `pose_merged` + auto `decision=Hit` (`buildservice.land_built_model`,
  `views.commit_model`); refinement → `Job` dispatched via `jobservice`, polled
  non-modally per-dataset, lands the refined PDB+MTZ on success and repoints
  `Dataset.current_model` / `current_sf`. The **map-of-record** evolves with it
  (dimple MTZ at ingest → servalcat MTZ after refine).
- **Inspect-drawer triage & navigation UX — DONE** (see §7): dataset-header
  chips, Sort dropdown, 3-state Active/With-events/All filter, prev/next nav
  across dataset boundaries, autobuild-backed event chips.
- **Per-event autobuild ingested — DONE**: `ingest_pandda2` parses each event's
  `events.yaml` `Build:` block into an event-scoped `LIGAND_POSE` artifact +
  `build_score`/`rscc`/`optimal_contour` on the Event (migration 0007;
  `test_event_autobuild.py`). Model-of-record settled: the pose is
  overlay/provenance; refinement targets the per-crystal `Dataset.current_model`.
- **Public dataset resolved & fetched**: BAZ2B vs Zenobia fragment library,
  Zenodo DOI 10.5281/zenodo.48768, **CC-BY-SA-4.0**, 201 datasets. Living at
  `~/Developer/pandda-data/BAZ2B-zenodo-48768/` (OUTSIDE the repo — ShareAlike
  ⇒ never vendor into git; reference by DOI + fetch script).
- **PanDDA2 run COMPLETE + schema reconciled** on that data (`pandda2.analyse`,
  Ray, 8 cpus, out_dir `…/pandda2_out`): 201 processed dirs, 309 events, 41
  sites. A separate `ingest_pandda2` management command reads the CSV+YAML
  format; the model gained `Event.score` (PanDDA2 `hit_in_site_probability`,
  the machine's ML opinion — distinct from human `decision`) and
  `Event.interesting`; the client gained a CCP4-map path
  (`loadToCootFromMapURL`). **The detailed engineering learnings from all this are in
  [CLAUDE.md](../CLAUDE.md) — read that for the *how*; this file is the *what's
  next*.**

## Priority order (agreed)

### 1. Public dataset — ✅ RESOLVED / FETCHED
BAZ2B, Zenodo 48768, CC-BY-SA-4.0, 201 datasets, fetched outside the repo.
Rule learned (from the 120 MB WASM purge): **do not vendor large data into git**
— reference by DOI + `scripts/` fetch; commit at most a tiny ingested slice.
Remaining: wire a documented fetch script + remove any private-data assumptions
from the default data path.

### 2. Ground-truth / artifact-storage model — ◧ SCHEMA + RECONCILE DONE
The prototype wrote built models to disk + updated Redux but never updated
`results.json` → drift. **Design in
[DESIGN-artifacts-and-jobs.md](DESIGN-artifacts-and-jobs.md)** (design of record
for #2, the `JobRunner` seam, and the two deployment bindings). **Implemented
so far (branch `artifact-lineage-jobs`):** DESIGN §4 items 1–2 —
- the schema (migration 0004): `Artifact.origin/parent/produced_by/created_at`,
  `Event`/`Dataset.current_model` + `inputs_changed`, and the `Job` model;
- the re-ingest reconciliation in `inspect_api/reconcile.py`, with both ingest
  readers refactored to parse-only (→ `ProjectSpec`) and delegate persistence;
  verified by `inspect_api/tests/test_reconcile.py` (5 tests) + real-data
  re-ingest (decision + built-model preservation confirmed on clean BAZ2B).
**Still to build:** the artifact-*producing* paths that USE these fields
(#4 ligand-build, #4b job runner). Decisions locked:
- **DB = ground truth**; lineage lives on `Artifact` itself (self-FK `parent` +
  `origin`/`produced_by`), not a separate version table. Pointers split by
  granularity: `Event.current_model` (built ligand) vs `Dataset.current_model`
  (refined whole pdb) — mirrors build-per-event / refine-per-crystal.
- **Write-once** is mechanical: built/refined = new row + new bytes; pointer
  moves, old row never mutated.
- **Imported JSON/CSV/YAML = frozen import artifacts** — never written back.
- **Reconciliation (the real core): re-ingest is additive + import-scoped** —
  replaces `origin=imported` rows, updates machine metrics, leaves human
  decisions and built/refined models untouched, and *flags* divergence
  (`inputs_changed`) rather than auto-resolving it ("surface, don't resolve").
Implementation order is in §4 of the DESIGN doc; schema (items 1–2) lands before
the job/build features (#4) so artifact-producing actions can't predate the
lineage model.

### 3. Back-to-app continuity — ◧ PARTIAL
A breadcrumb (Home icon + project `Link`) exists in the inspect side panel
(`InspectDrawer.tsx`). The original ask — a floating "← Back to {project}" Fab
on the full-bleed canvas itself — is **not** done; the breadcrumb covers the
need adequately, so this is low-priority polish now, not a gap.

### 4. "Add current ligand at current location" → auto-swap decision to Hit — ✅ DONE
**Shipped.** The inspect drawer's **Merge ligand** button (on a candidate pose)
exports the merged PDB and calls `commitModel(ev, merge=true)`;
`views.commit_model` → `buildservice.land_built_model` registers an
`Artifact(origin=built, parent=<input>)`, repoints `Event.current_model`, sets
`Event.pose_merged=True`, and auto-fires `decision=Hit` when the event was
unreviewed. Same artifact contract as a dispatched job, different producer —
exactly as designed in
[DESIGN-artifacts-and-jobs.md](DESIGN-artifacts-and-jobs.md) §2.2. Covered by
`test_event_autobuild.py`.

### 4b. Refine dispatch / tracking + Electron binding — ✅ DONE (compose binding → see Next steps)
**Shipped.** A real `Job` model + `JobRunner` seam: `submitRefine` dispatches a
refinement of the dataset's `current_model` vs its MTZ; `jobservice` runs it
(servalcat by default), and `refresh_job._land` idempotently (`select_for_update`)
lands the refined PDB+MTZ and repoints `Dataset.current_model` / `current_sf`.
The client polls **non-modally, per dataset** (`pollRefineJob`, `jobsByDataset`)
so you keep inspecting while it runs, and reloads the 3D model on completion if
you're still on that crystal. The **Electron binding** (frozen-Python backend +
CI-built signed installers) is likewise done — it's the shipped Reinspect app.
**Still future:** the **docker-compose binding** (`SharedVolumeRunner` + sidecar)
— design-complete, promoted to [Next steps](#next-steps).

### 5. Real PanDDA2 analysis + reconcile data model — ✅ DONE (run + row-level diff complete)
The BAZ2B run finished (309 events, 41 sites); a separate `ingest_pandda2`
reader is in the repo; `Event.score`/`interesting` added. **Full engineering
detail (verified CSV columns, the data-quality caveats, the
recentre/contour/isEM Moorhen lessons) is in [CLAUDE.md](../CLAUDE.md).** Summary
of findings (2026-05-30, from running pandda2 + reading the editable source at
`~/Developer/pandda2/pandda_2_gemmi`):
- **PanDDA2 never writes `results.json`** (0 refs in source). Our PanDDA1-shaped
  ingest cannot parse PanDDA2 output as-is → need a **second ingest reader**
  (the import-boundary abstraction absorbing this is the design working).
- PanDDA2 output vocabulary: global `pandda_analyse_events.csv` +
  `pandda_analyse_sites.csv` (+ `pandda_inspect_*` = inspect-writable copies);
  per-dataset `events.yaml` + `processed_dataset.yaml`; `shells.json`,
  `events.json`, `autobuild.json`, `pandda_log.json`; `analyses/html_summaries/`.
- Output layout: `<out>/processed_datasets/<dtag>/` with `events.yaml`,
  `processed_dataset.yaml`, `<dtag>-z_map.native.ccp4`,
  `<dtag>-event_N_1-BDC_<x>_map.native.ccp4`,
  `<dtag>-ground-state-average-map.native.ccp4`, `xmap.ccp4`,
  `<dtag>_event_N_best_autobuild.pdb`, plus `autobuild/ model_maps/
  modelled_structures/ ligand_files/`. Inputs symlinked as
  `<dtag>-pandda-input.pdb/.mtz`.
- **Impact**: internal Dataset/Event/Artifact/Shell model **survives**; what
  changes = (a) CSV+YAML reader (not JSON), (b) sites are still derived from
  bare `site_num` (a **first-class Site entity** is not built — it backs the
  "tab per site" UI in [N1](#n1-sites-tab--browse-crystals-filtered-by-site)),
  (c) different artifact-discovery paths.
- **DEFERRED**: row-level diff of `pandda_analyse_events.csv` / `_sites.csv`
  against our schema — those globals are written only at the END in `analyses/`.
  Revisit when `analyses/` populates. (System python3.14 lacks `yaml`; use the
  pandda2 conda-env python to parse YAML.)

### 6. Electron full-stack app — ✅ DONE / SHIPPED (v0.1.1)
The **laptop binding of the same contract architecture**: the frozen Django
backend + SQLite + built client, wired to `LocalFileStore` + the local job
runner, packaged as **Reinspect**. Electron's main process spawns the frozen
backend and points a window at it; electron-builder produces signed/notarized
installers via a mac/win/linux CI matrix; a tag publishes a GitHub Release. One
codebase serves desktop and (future) hosted with no divergence. Detail:
[packaging/README.md](../packaging/README.md). Open polish: arm64-only macOS
(no Intel/universal yet), Windows unsigned.

### 7. Inspect-drawer triage & navigation UX — ✅ DONE (2026-05-31, main @ e330b6c)
The grouped accordion drawer became a real triage surface, all client-side in
`client/src/components/InspectDrawer.tsx` + `client/src/grouping.ts`:
- **Dataset-header chips**: #events, `built` (event has an autobuilt ligand
  pose), #hits, and quality `Q X%` = best 1−BDC across the dataset's events.
- **Sort dropdown**: name (numeric-aware) | #events | autobuilt | best quality.
- **3-state filter**: Active → With events → All. *Active* hides datasets whose
  every event is `no_hit` ("finished triaging as dead").
- **Navigation**: prev/next step through the flattened filtered+sorted event
  order **across dataset boundaries** (icon-only `< >` + `i/N` counter); the
  accordion **follows the live event** — opens the new dataset, closes the old.
- **Per-event autobuild surfacing**: event chips backed by a `LIGAND_POSE` get
  a build icon + info-tinted border + RSCC in the tooltip; the contour slider
  seeds from the event's `optimal_contour`.
- Ingest side (the `LIGAND_POSE` + build-metrics parsing this UX reads) is
  tested in `inspect_api/tests/test_event_autobuild.py`. Client UI itself is
  untested — a Vitest setup is a later add.
- **Since shipped** (was "not yet" here): the build + refine actions (#4/#4b)
  are now live — merge a pose to a built model, dispatch a refinement, model +
  map update on completion.

## Next steps

The core inspect→build→refine loop and the desktop app are done; these are the
features that make it a *campaign* tool and broaden its reach. Roughly ordered
by value-for-effort. Each notes what already exists to build on.

### N1. "Sites" tab — browse crystals filtered by site
A binding **site** is a recurrent location seen across many crystals; today it's
only `Event.site_num` + derived per-site summaries (no first-class entity — the
audit confirmed no `Site` model). Promote it: group the event/crystal list **by
site**, so you can review "every crystal that has an event at site 3" in one
pass. Scaffolding already exists — `client/src/grouping.ts` declares
`GroupAxis = "dataset" | "site"` and the dashboard already plumbs `n_sites` +
per-site distributions. Needs: a site axis in the drawer (group by `site_num`,
derive the centroid from member events — the CSV centroids are often `(0,0,0)`),
and a per-site header. A backend `Site` entity/endpoint (ROADMAP §5's deferred
item) is optional v1 — derived grouping suffices to start.

### N2. Summary Moorhen views — campaign & per-site overviews
Generate Moorhen scenes that *summarise* rather than inspect one event:
(a) a **campaign view** and (b) a **per-site view** — protein in **ribbon** plus
**all built ligands** drawn as properly-bonded sticks (CBs), so you see the
whole fragment-binding picture at a glance. Mechanically feasible now:
`moorhen-shim.ts` exposes `addRepresentation(style, cid)` (ribbon) and the
per-pose `loadToCootFromURL` + `fetchIfDirtyAndDraw("CBs")` path already used in
the drawer. Needs: an endpoint returning the model + the set of built ligand
poses for a project/site, and a viewer mode that loads them together. Pairs
naturally with N1 (a site's view = its members' ligands on the reference model).

### N3. Export to the legacy pandda.inspect project shape
*(was a parked idea — promoting it.)* Downstream tools (Fragalysis /
XChemExplorer) expect the legacy pandda.inspect-ed layout: versioned
`modelled_structures/<dtag>-pandda-model.pdb` (+ symlink-to-latest) and the
`pandda_inspect_events.csv` decision columns. Our durable state is the DB
(`Dataset.current_model` lineage + `Event.decision`/`pose_merged`), so this is
an **export adapter** — the *reverse* of the ingest import-boundary, same
principle (filesystem is a projection, DB is truth): an `export_pandda_inspect`
command (and a UI/CLI trigger producing a downloadable artifact) materialising
`current_model` → the legacy model path and decisions → the inspect CSVs. The
cleanest interop story; scope to the columns a real consumer needs.

### N4. docker-compose binding — prove "code once, deploy many"
*(promoted from #4b; the audit found it design-complete but unimplemented.)*
The **second `JobRunner` binding**: the same backend as `web` + an amd64 CCP4
`runner` sidecar sharing a volume, with a `SharedVolumeRunner` (job-as-file
queue; `LocalProcessRunner`'s status-file wrapper already "pre-proves" the
pattern). This is the highest-signal architecture demo — it shows the
`DataStore`/`JobRunner` seam delivering desktop **and** server from one codebase
with only the binding swapped. Fully specced in
[DESIGN-artifacts-and-jobs.md](DESIGN-artifacts-and-jobs.md) §3.2–3.3; needs
`compose.yml`, Dockerfiles, and the `SharedVolumeRunner` class. (Note the
overlap with the Materia/R6 work below — both exercise the path-resolution
seam.)

### N5. Decision provenance (who / when)
Surface **`inspected_by` + a timestamp** on each decision — legacy
pandda.inspect records this, and it's the prerequisite the API-first,
multi-reviewer thesis actually rests on. Small (a couple of fields + serializer
+ a line in the drawer), but it's what turns "a decision" into "an auditable
review trail."

### N6. Keyboard-driven triage
The drawer already steps prev/next across events; add **hit / miss / unsure
hotkeys** (+ next) so a reviewer can rip through a campaign without the mouse.
Pure UX, high daily value, no backend change.

### N7. Re-ingest diff view
Reconciliation already computes `inputs_changed` flags when a re-ingest /
PanDDA-rerun diverges from prior state ("surface, don't resolve") — but nothing
in the UI **shows** it. A "what changed since the last PanDDA run" view makes
the (already-built, currently invisible) re-ingest safety story visible.

### N8. 2D ligand gallery navigation
*(parked idea, still apt.)* Once events are *interpreted* (a built ligand = a
real entity), offer an RDKit 2D-sketch gallery as primary navigation — "jump to
the crystals where compound X was built." Distinguish *soaked* (data/) vs
*modelled* (models/) compounds; needs the built-event state N5/N1 surface.

## Parked ideas (revisit later)
*(Export, the docker-compose binding, and the 2D-ligand gallery moved up to
[Next steps](#next-steps); these remain genuinely parked.)*

- **JobRunner progress reporting** (depends on #2): light up
  `JobRunner.status()/logs()` — the most architecture-revealing feature, since
  *how you get progress* is the most backend-specific part (local tail vs qsub
  logfile vs cloud API). PanDDA2 progress signal = `processed_datasets/*` count
  vs input count (backend-agnostic); it has no clean machine-readable stream.
- **Backend-fit studies (peer candidates)**: evaluate how cleanly PanDDA's
  event/site model and compute needs map onto established platforms that could
  implement the `DataStore`/`JobRunner` contract — each a first-class candidate,
  studied on equal terms:
  - **CCP4Cloud** — its project/job and data-management model as a hosted
    backend and execution environment.
  - **CCP4i2** — its Job/File schema and task framework as a backend.

  Each study should be rigorous and even-handed, so any conclusion is earned
  rather than assumed; the aim is to find the cleanest path to interoperating
  with whichever platform(s) a deployment already uses.

  **First concrete instance — Materia (embeds CCP4i2):** the repo-side
  obligations for being incorporated by Materia are written up in
  [MATERIA_INTEGRATION.md](MATERIA_INTEGRATION.md) (companion to Materia's own
  proposal). Net: the `DataStore`/`JobRunner` protocols already exist, so most
  requirements are confirmations; the real work is the **R6 refactor** (route
  *all* path resolution through `DataStore` — `jobservice`/`buildservice`/the
  download view bypass it today), gated by **Q2** ("where does PanDDA2 run",
  which decides whether artifacts are born as relpaths or CCP4i2 uuids). Auth is
  ratified as a *seam* (`PANDDA_AUTH_BACKEND`, open `local` default), **not** a
  hard `ccp4i2-api` dependency. Sequence: Q2 → R6 → R0 registry/factories →
  binding plugins.

## Background context (not to lose)
- **Strategy**: this is an API-first reference for pandda.inspect. The API
  contract is the deliverable; storage and compute backends
  (`DataStore`/`JobRunner`) are swappable implementations; clients (Moorhen web,
  Coot, CCP4i2, CLI) consume the same contract. The aim is to keep the contract
  stable and backend-neutral so any platform — CCP4Cloud, CCP4i2, and others very
  much included — can serve or be served by it. Demonstrations should stay
  implementation-neutral and emphasise interoperability over any one stack.
- **Collaboration posture**: this work aims to complement the CCP4 ecosystem and
  the wider pandda.inspect effort, not compete with it. Keep the contract and any
  demonstrations backend-neutral so the project interoperates cleanly with
  CCP4Cloud, CCP4i2, and other tools — the goal is a shared, stable contract that
  any of them can adopt or serve.
