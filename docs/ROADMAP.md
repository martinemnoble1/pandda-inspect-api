# pandda-inspect-api — Roadmap & status

Prioritised next steps and where we are against them. Companion to
[README.md](../README.md) (overview), [CLAUDE.md](../CLAUDE.md) (engineering
*how* / gotchas), [docs/RATIONALE.md](RATIONALE.md) (the contract-first *why*),
and [client/PANDDA2_INTEGRATION.md](../client/PANDDA2_INTEGRATION.md)
(Moorhen/PanDDA2 client specifics). This file is the single place for *what's
next and in what order*.

Last updated: 2026-05-31.

## Where we are (snapshot)

- **MVP works end to end**: Django+DRF backend (Project/Dataset/Event/Artifact/
  Shell) + React/Moorhen client (landing, import, project browser, dashboard
  with report iframes, Moorhen inspect view with grouped accordion drawer,
  contour control, decision PATCH). Public repo, clean history.
- **Public dataset resolved & fetched**: BAZ2B vs Zenobia fragment library,
  Zenodo DOI 10.5281/zenodo.48768, **CC-BY-SA-4.0**, 201 datasets. Living at
  `~/Developer/pandda-data/BAZ2B-zenodo-48768/` (OUTSIDE the repo — ShareAlike
  ⇒ never vendor into git; reference by DOI + fetch script).
- **PanDDA2 run COMPLETE + schema reconciled** on that data (`pandda2.analyse`,
  Ray, 8 cpus, out_dir `…/pandda2_out`): 201 processed dirs, 309 events, 41
  sites. A separate `ingest_pandda2` management command reads the CSV+YAML
  format; the model gained `Event.score` (PanDDA2 `hit_in_site_probability`,
  the machine's ML opinion — distinct from human `decision`) and
  `Event.interesting`; the client gained a CCP4-map path (`loadToCootFromMapURL`)
  and a `SiteView`. **The detailed engineering learnings from all this are in
  [CLAUDE.md](../CLAUDE.md) — read that for the *how*; this file is the *what's
  next*.**

## Priority order (agreed)

### 1. Public dataset — ✅ RESOLVED / FETCHED
BAZ2B, Zenodo 48768, CC-BY-SA-4.0, 201 datasets, fetched outside the repo.
Rule learned (from the 120 MB WASM purge): **do not vendor large data into git**
— reference by DOI + `scripts/` fetch; commit at most a tiny ingested slice.
Remaining: wire a documented fetch script + remove any private-data assumptions
from the default data path.

### 2. Ground-truth / artifact-storage model — ◧ DECIDE NEXT (foundation for #4)
The prototype wrote built models to disk + updated Redux but never updated
`results.json` → drift. Principle to implement:
- **DB = ground truth** for mutable state, incl. a *pointer to current-best
  artifact*.
- **Artifact bytes in the DataStore, versioned / write-once** (never overwrite;
  write v2, repoint the DB).
- **Imported JSON/CSV/YAML = read-only frozen import artifacts** — never written
  back. No drift *if* the filesystem stops being a source of truth post-ingest.
- **Hard residual**: re-ingest / PanDDA-rerun **reconciliation policy** — does a
  rerun wipe / merge / version human-built models? This is the real design core.
This is a *decision*, best made deliberately before building #4.

### 3. Back-to-app continuity — ○ QUICK WIN
InspectPage is full-bleed (`position:absolute; inset:0`) so app chrome vanishes.
Add a floating "← Back to {project}" Fab top-left (high z-index) — NOT a full
AppBar (it steals canvas height). ~15 lines.

### 4. "Add current ligand at current location" → auto-swap decision to Hit — ○ FEATURE (after #2)
Reuse the prototype Coot-call IDEA (proven on Moorhen 0.23):
`cootCommand get_monomer_and_position_at ["LIG", molNo, ...origin negated]`
→ `theMolecule.fitLigand(activeMap.molNo, ligandMol.molNo, …)`
→ `merge_molecules` → redraw. Building a ligand *is* the hit assertion → fire
the decision PATCH automatically. **Produces the artifact #2 governs — don't
build before #2 settles, or it recreates the drift bug.**

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
  changes = (a) CSV+YAML reader (not JSON), (b) add a **first-class Site entity**
  (PanDDA2 has a sites table; we only had bare `site_num` — also backs the
  "tab per site" UI; cf. `SiteView.tsx`), (c) different artifact-discovery paths.
- **DEFERRED**: row-level diff of `pandda_analyse_events.csv` / `_sites.csv`
  against our schema — those globals are written only at the END in `analyses/`.
  Revisit when `analyses/` populates. (System python3.14 lacks `yaml`; use the
  pandda2 conda-env python to parse YAML.)

### 6. Electron full-stack app — ○ DESKTOP PACKAGING
Many users want a self-contained laptop/desktop install. Electron is simply the
**laptop binding of the same contract architecture**: bundle the backend +
SQLite + client, wired to `LocalFileStore` + `LocalProcessRunner`. The value is
that one codebase serves both a desktop install and a hosted deployment with no
divergence — desktop and cloud from the same contract.

## Parked ideas (revisit later)
- **RDKit "navigate by built compound" gallery**: once events are *interpreted*
  (built ligand = real entity), offer a gallery of 2D sketches as primary
  navigation. Needs an "interpreted/built" event state first. Distinguish
  *soaked* compound (data/) vs *modelled* compound (models/) — they can differ.
- **JobRunner progress reporting** (#7-ish, depends on #2): light up
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
