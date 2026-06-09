# ADR: Multi-run data model — Crystal / RunDataset / Observation / Finding

- **Status:** **Phase A + B + C shipped; Phase E (compare UI, first cut)
  in progress.** Phase A: `Event.detection_centroid` (#32). Phase B (#33):
  `RunDataset`, `Event.run_dataset`, metrics off `Dataset`, reconcile
  run-scoped. Phase C (#34): `Finding` (run-independent decision anchor) +
  spatial association. Phase E: a `Finding` grouping axis in the client (per-run
  observation chips under each binding site, one shared decision) + the additive
  `run_id`/`run_group`/`finding` Event API fields. **Phase D (frame guard) was
  assessed and DROPPED** — association already degrades gracefully under a frame
  mismatch (mismatched-frame centroids never fall within the 1.5 Å tolerance, so
  they seed separate Findings rather than false-merging), and the cheap signal
  (apo `CRYST1`) is the native cell, identical across runs regardless of
  alignment, so it wouldn't detect the case it targeted. **Side-by-side per-run
  columns deferred to Phase F.** Empirical basis validated against two real
  BAZ2B runs (below).
- **Phase C scope (decision-only, by decision):** only the decision/review
  state (`decision`, `confidence`, `comment`, `inspected_*`) moved to `Finding`.
  `Event.current_model` / `pose_merged` (the built-ligand pointer) stayed on
  `Event` — they are entangled with buildservice/jobservice and less central;
  defer to a later phase. `Event` keeps read-through proxy properties
  (`event.decision` etc. → `event.finding`), and the API write path (PATCH +
  `commit_model`) routes to `event.finding`, so the contract/client are
  unchanged. Association keys on `detection_centroid` (fallback `xyz_centroid`)
  at `MATCH_TOLERANCE_A = 1.5`; eager seeding mints a candidate Finding per
  unmatched site. Historical data blatted again (migration `0018`).
- **Date:** 2026-06-09.
- **Phase B refinements (vs the original sketch below):** (1) `current_sf`
  STAYS on `Dataset` (crystal grain) — it is the map analogue of the
  crystal-grain `current_model`, so by parity it is not run-scoped; only the six
  pure analysis metrics moved to `RunDataset`. (2) `Event.dataset` is KEPT as a
  denormalised crystal pointer alongside the authoritative `Event.run_dataset`,
  so the many `event.dataset` / `dataset.events` callers are unaffected. (3) The
  single-run API is bridged via `Dataset.primary_run_dataset` (the latest run's
  analysis) until the Phase E compare UI lands — no client change in Phase B.
  (4) Standalone/CLI ingest synthesises a deterministic `Run` keyed on
  `(project, source_root)` so re-ingest stays idempotent.
- **Relates to:** [RUN_LIFECYCLE.md](RUN_LIFECYCLE.md) (the `Run` model this
  builds on), [DESIGN-artifacts-and-jobs.md](DESIGN-artifacts-and-jobs.md) §1.3
  (the re-ingest "surface, don't resolve" policy this reshapes), and
  `inspect_api/reconcile.py` (the module that mostly dissolves).
- **Supersedes:** the open question flagged in CLAUDE.md — *"The reconciliation
  policy for re-ingest / PanDDA-rerun is an open design question."* This ADR
  answers it for the **different-run** case (as distinct from idempotent
  re-ingest of the *same* run's tree).

## Context — the hole

`Run` ([models.py](../inspect_api/models.py#L417)) is **provenance-only**:
nothing it produces points back to it. The analytical output is keyed entirely
run-independently —

- `Dataset` → unique `(project, dtag)`
- `Event` → unique `(dataset, event_num)`

So when a second run touches the same `dtag`,
[`_reconcile_events`](../inspect_api/reconcile.py#L202) does
`get_or_create(dataset=…, event_num=…)` and **overwrites the machine columns in
place**. Last-write-wins; the earlier run's analytical conclusion is gone — not
versioned, not comparable. The `Run` row survives describing output you can no
longer see.

Two distinct defects hide here:

1. **No run attribution.** The schema can physically hold only one conclusion
   per `(project, dtag)`, even though `Run` exists precisely to express that
   there can be many (parameter sweeps, two autobuild strategies, an
   interrupted re-run over a subset).
2. **The merge key is an unstable ordinal.** `event_num` is PanDDA's per-run
   `event_idx`. Two runs can find a different *number* of events and number
   them differently, so `event #1` from run A and `event #1` from run B can be
   *physically different blobs*. The "human-preserve" guarantee in
   `_reconcile_events` is therefore sound only for **idempotent re-ingest of the
   same run**, and silently unsafe across genuinely different runs — it can
   re-bind a curator's "hit" onto a different physical event.

**Decision (with Martin, 2026-06-09): compare-and-keep-both.** Runs are peers;
disagreement is scientific signal to be surfaced, not flattened. That choice
pays for the larger refactor below.

## Empirical basis (two real BAZ2B runs)

Validated on `/Volumes/LocalStore/pandda/pathx_runs/`:

- `A_DEfull_baz2b` — full run, 58 datasets with events, has the global
  `analyses/pandda_analyse_events.csv`.
- `C4_crowtherrefine_baz2b` — **interrupted mid-run**, 36 datasets with events
  (a strict subset of A's), a *different* autobuild strategy. Because the global
  CSV is written at end-of-run, it has **none** — only per-dataset
  `processed_datasets/<dtag>/events.yaml`. Still fully usable.

Across the 13 events matchable over the 36 shared crystals:

| Signal | Cross-run behaviour |
|---|---|
| **Frame** | Both runs' apo `*-pandda-input.pdb` are coord-identical (CA centroid `23.38, 28.31, 26.68`; same `CRYST1 … C 2 2 21`). Per-`dtag` coords are directly comparable across runs — **no superposition.** |
| **Detection locus** (centroid of `events.yaml` `Position Array` voxels) | median **0.00 Å**, 11/13 **< 1 Å**. The runs agree almost exactly on *where the blob is*. |
| **Autobuild pose centroid** | median **21 Å**, **all 13 > 5 Å** — poses land far apart *even when detection is 0.00 Å apart* (the two strategies diverge wildly). |

Three findings that **shape the schema**:

1. **The detection centroid is the run-stable anchor** (sub-Å). The autobuild
   pose is the *worst* possible match signal — keying association on pose coords
   or pose-RMSD would shatter every agreeing event into two Findings exactly
   when the runs most disagree. → **Match on the detection centroid; pose RMSD
   is an *agreement metric*, not a key.**
2. **`events.yaml`'s top-level `Centroid` is build-contaminated** — it equals
   the *Build* `Centroid`, and the CSV `x,y,z` equals it too. When the build
   wanders, so does the reported centroid (20 Å). The current ingest lifts this
   into `Event.xyz_centroid`
   ([ingest_pandda2.py:248](../inspect_api/management/commands/ingest_pandda2.py#L248)),
   so `xyz_centroid` is unreliable as a detection locus. → **Derive the
   detection locus from the `Position Array` voxels.**
3. **The unstable-ordinal bug is real in the wild:** A `event 1` ↔ C4 `event 2`
   (xtal-0017), `event 3` (xtal-0018, xtal-0032). Nearest-detection matching
   pairs them correctly; `event_num` mis-pairs them.

Two genuine *detection* disagreements (xtal-0003 at 21.7 Å, xtal-0030 ev2 at
16.3 Å — both weak, low-RSCC) correctly seed **separate** Findings ("only A
found a blob here / only C4 found one there"). The gap between real matches
(≤ 1 Å) and real disagreements (≥ 16 Å) is enormous, so the tolerance is robust.

## The grain separation

Today `Dataset` and `Event` each carry a mix of run-independent and run-varying
state. Compare-and-keep forces a split along that seam:

| Grain | Run-independent? | Holds |
|---|---|---|
| **Crystal** (`dtag`) | yes | identity, `ligand_source`, the curated whole-crystal `current_model` |
| **Run** | — | params, status, provenance (exists today) |
| **Run × Crystal** | no | *that run's* resolution, R-factors, `map_uncertainty`, apo input artifacts, `current_sf` |
| **Observation** (an event) | no | machine detection: detection centroid, score, BDC, z-peak, site, autobuild pose + scores |
| **Finding** (binding conclusion) | yes | `decision`, confidence, comment, inspector, accepted ligand model, `pose_merged` |

The key realization: `Dataset`'s metric fields (`analysed_resolution`,
`r_free`, `r_work`, `map_uncertainty`, `current_sf`) are **not** crystal
properties — they are outputs of *one run* processing that crystal. And the
human `decision` is a property of *the binding site on the crystal*, not of any
one run's detection of it.

## Target schema

```
Dataset            # KEEP as the run-independent CRYSTAL anchor
  (project, dtag) unique
  ligand_source
  current_model -> Artifact     # whole-crystal curated model — CRYSTAL grain (decided)
  #  REMOVE: analysed_resolution, high/low_resolution, r_free, r_work,
  #          map_uncertainty, current_sf   (these move to RunDataset)

RunDataset         # NEW: one run's analysis of one crystal
  run     -> Run     (CASCADE)
  dataset -> Dataset (CASCADE)
  (run, dataset) unique
  analysed_resolution, high_resolution, low_resolution, r_free, r_work, map_uncertainty
  current_sf -> Artifact        # this run's dimple map-MTZ
  # apo *-pandda-input.pdb/.mtz artifacts scope here (Artifact.run_dataset)

Event              # = run-scoped OBSERVATION
  run_dataset -> RunDataset (CASCADE)   # FK MOVES off Dataset
  (run_dataset, event_num) unique       # event_num is now a TRUE per-run ordinal
  detection_centroid (JSON)             # NEW: from Position Array voxels — the match key
  xyz_centroid, xyz_peak                # kept; build-snapped, NOT the match key
  score, interesting, bdc, z_peak, z_mean, site_num, cluster_size,
  build_score, rscc, optimal_contour
  finding -> Finding (SET_NULL, null)   # spatial link; set by ingest/curator
  # LIGAND_POSE + EVENT_MAP artifacts scope here (already event-scoped)
  #  REMOVE (move to Finding): decision, confidence, comment, inspected_by/_oid/_at,
  #          current_model, pose_merged

Finding            # NEW: run-independent human conclusion, anchored in space
  dataset -> Dataset (CASCADE)          # CRYSTAL-scoped
  centroid (JSON)                       # representative detection locus (for matching)
  decision, confidence, comment, inspected_by/_oid/_at
  current_model -> Artifact (SET_NULL)  # accepted ligand pose (was Event.current_model)
  pose_merged
  pose_rmsd (float, null)               # agreement metric: built poses of linked Events,
                                        #  computed when >=2 linked Events have a LIGAND_POSE
  # reverse: finding.events = the Observations (across runs) supporting it
```

`Artifact` gains an optional `run_dataset` FK (parallel to its existing
`project`/`dataset`/`event` scope FKs) so per-run apo inputs and `current_sf`
targets attach to the right run.

## What this buys

Disagreement becomes a first-class, queryable shape:

- **Agreement** — a `Finding` with `Event`s from both runs (+ a small `pose_rmsd`).
- **Pose conflict** — a `Finding` with `Event`s from both runs but a large
  `pose_rmsd` (same site, conflicting build; the per-Event `rscc` says which to
  trust — e.g. xtal-0020: A rscc 0.48 vs C4 0.11).
- **Run-A-only** — a `Finding` whose only `Event` is run A's → "B missed it."
- **Unclaimed** — an `Event` with `finding=null` → a candidate nobody judged.

A site-by-site "run A vs run B" diff for a crystal is one join.

## Association algorithm (eager seeding — decided)

At ingest, after Observations for a run are upserted:

1. For each Observation, find the nearest existing `Finding.centroid` for the
   same `Dataset` within **`MATCH_TOLERANCE_A` (start 1.5 Å)**, using plain
   Euclidean distance in the shared native frame (no superposition).
2. **Hit** → link `Event.finding`; if the Finding now has ≥ 2 linked Events with
   a `LIGAND_POSE`, (re)compute `pose_rmsd`.
3. **Miss** → create a new candidate `Finding` at the Observation's detection
   centroid, `decision = unreviewed`, and link it. (This is the *eager* seeding:
   the compare view is populated before any curation.)

`Finding.centroid` is the detection locus of its first/representative
Observation; it never moves with a wandering build. Matching keys on the
**Observation `detection_centroid`** (sub-Å run-stable), never on pose coords.

**Frame guard (cheap, do it):** cross-run centroid comparison is only valid
because the apo inputs share a frame. At ingest, assert the new RunDataset's apo
`CRYST1` cell matches the Dataset's other RunDataset apo cells (within tol); if
not, **do not auto-associate across runs** for that crystal — surface it. This
protects against a future PanDDA build that emits aligned-frame coords.

## What changes in ingest / reconcile

`reconcile.py` mostly **dissolves** — its machine-overwrite/human-preserve
gymnastics exist only because human state lives on `Event`. Once human state is
on `Finding` (which ingest **never touches**):

- `_upsert_dataset` → upserts the `Dataset` (crystal: `ligand_source` only) +
  the `(run, dataset)` `RunDataset` (all metrics, `current_sf`).
- `_reconcile_events` → upserts Observations by `(run_dataset, event_num)`.
  Within one run `event_num` is genuinely stable, so the merge key is finally
  sound; **re-ingesting the same run is idempotent, a different run never
  collides.** No human-preserve branch needed.
- The pointer/flag policy (`_apply_pointer_policy`, `inputs_changed`) narrows:
  `Dataset.current_model` (crystal model) and `Finding.current_model` (accepted
  pose) are the only human/job pointers to protect; `current_sf` moves to
  RunDataset and is plain imported state.
- New step: the **association pass** above.

Ingest also starts populating `Event.detection_centroid` from the `events.yaml`
`Position Array` voxel centroid (new parse in
[ingest_pandda2.py](../inspect_api/management/commands/ingest_pandda2.py) near
the existing `xyz_centroid` build), and must **create/attach a `Run`** (today
ingest is Run-agnostic; the `/runs/` flow creates `Run` then ingests — the two
must meet so every RunDataset has a Run).

## Migration plan (phased, each phase shippable)

**Phase A — detection locus (independent; fixes a real bug now).**
- Migration: add `Event.detection_centroid` (JSONField, default `list`).
- `ingest_pandda2`: compute centroid from `Position Array`; store it. Keep
  `xyz_centroid` as-is but document it as build-snapped.
- Backfill: not possible from the DB (voxels aren't stored) — it populates on
  the next ingest. Existing rows stay `[]` until re-ingested. *No behaviour
  change yet; this just makes the future match key available and corrects "where
  is the event".*

**Phase B — run-scope the analysis grain. ✅ SHIPPED.**
- DONE differently from the sketch: by decision we **blatted all historical
  analysis data** (migration `0017` wipes Artifact/Job/Event/Shell/Dataset/Run,
  keeps Projects) rather than synthesise a legacy `Run` and backfill. Much
  simpler, no migration-safety logic.
- `RunDataset(run, dataset)` carries the six metrics. `Event.run_dataset` added
  (nullable in schema; always set by ingest), unique key swapped to
  `(run_dataset, event_num)`. `Event.dataset` kept as denormalised crystal
  pointer. `current_sf` did NOT move (stayed crystal-grain — see refinement #1).
- `Artifact.run_dataset` was NOT needed in Phase B (apo inputs stay
  dataset-scoped; revisit only if per-run input artifacts must diverge).
- Reconcile is the single choke point: it accepts a `run` (synthesised
  deterministically for standalone ingest), upserts the `RunDataset`, and keys
  events on `(run_dataset, event_num)`. PanDDA1's reader was untouched — it
  flows through the same reconcile and gets a synthetic run for free.

**Phase C — Finding (human anchor). ✅ SHIPPED (decision-only).**
- DONE differently: data blatted again (`0018` wipes), so no per-row backfill —
  `Finding` + `Event.finding` added, the decision/review columns dropped off
  `Event`. The built-model (`current_model` / `pose_merged`) did NOT move
  (deferred — see scope note up top).
- Association lives in `reconcile._associate_findings`, called per dataset after
  events upsert: each event matches the crystal's nearest `Finding.centroid`
  within `MATCH_TOLERANCE_A` (1.5 Å) on its `detection_centroid` (fallback
  `xyz_centroid`), else eager-seeds a new unreviewed `Finding`. Findings are
  never mutated by ingest — only created/linked — so human state is out of the
  import path. The shared `Finding` is the cross-run decision channel.
- API unchanged: `Event` read-through proxy properties + serializer `update()` /
  `commit_model` write to `event.finding`. The summary's decision counts moved
  to `finding__decision` lookups.
- Open: the **frame guard** (assert apo `CRYST1` matches before cross-run
  association) is NOT yet implemented — fine while all ingests share a native
  frame, but a prerequisite before trusting association across differently
  aligned runs. Tracked for Phase D.

**Phase D — reconcile rewrite + association.**
- Rewrite `reconcile.py` per "What changes" above; add the association pass and
  the frame guard. Bulk of the old preserve-logic deleted.

**Phase E — API + client (out of scope here; flagged).**
- Serializers/endpoints surface `RunDataset` and `Finding`; the client
  event-chip/drawer reshapes around runs (a Finding groups per-run Observations;
  the drawer shows per-run maps/poses + `pose_rmsd` + `rscc`). This may feed
  back into the schema — see Open questions.

## Open questions

1. **`MATCH_TOLERANCE_A`** — start 1.5 Å (the 0.07–0.91 Å real matches vs
   16–22 Å disagreements give huge margin); tune against more multi-run pairs.
2. **`pose_rmsd` correspondence** — needs symmetry/atom-correspondence-aware
   RMSD (a flipped ring shouldn't read as 3 Å). Gate on same ligand id; skip
   (null) when correspondence is ambiguous.
3. **Multi-event ambiguity** — nearest-centroid within tol is greedy; if two
   Observations from one run both fall near one Finding (rare given the gap),
   prefer the closer and leave the other to seed its own. Revisit if it bites.
4. **Client reshape** — the Finding-centric compare UI is unspecified and is the
   most likely source of schema feedback. Design before Phase E freezes the
   serializers.
5. **Legacy `Run` provenance** — the synthesized "legacy-import" Run is a
   placeholder; real historical params are unrecoverable. Acceptable.
