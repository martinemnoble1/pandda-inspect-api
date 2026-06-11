# ADR: Deletion & cleanup — projects, runs, and the files behind them

- **Status:** **Implemented** on `feat/delete-cleanup-endpoints`. Chunk 1:
  storage-seam `delete()`, `Project.source_managed`, `cleanup.delete_run`,
  `DELETE /runs/<id>`, and the submit-side zombie guard. Chunk 2:
  `Project.archived`, `cleanup.archive_project`/`purge_project`,
  `DELETE /projects/<id>` (archive), `POST /projects/<id>/purge`,
  `POST /projects/<id>/unarchive`. **2026-06-11:** reviewed against Materia's
  delete-endpoint brief — §4 records the agreed API shape and five corrections,
  all encoded and tested (`inspect_api/tests/test_delete_cleanup.py`).
- **Date:** 2026-06-10 (§4 added 2026-06-11; implemented 2026-06-11).
- **Relates to:**
  [DESIGN-artifacts-and-jobs.md](DESIGN-artifacts-and-jobs.md) (the `Artifact`
  `origin`/`relpath` model this leans on),
  [RUN_LIFECYCLE.md](RUN_LIFECYCLE.md) (the `Run` model and the
  `share_path`→`out_dir` invocation contract),
  [MULTI_RUN_DATA_MODEL.md](MULTI_RUN_DATA_MODEL.md) (the
  `RunDataset`/`Finding` split that makes run-delete non-trivial), and
  `inspect_api/storage.py` (the `DataStore` seam that must grow a `delete`).
- **One-line thesis:** the database half of deletion is already free (cascades
  are wired); the *file* half is the whole design, and the discriminator that
  makes it safe is **`Artifact.origin`** plus **`Run.out_dir` provenance** —
  never the filesystem path alone.

---

## Context — what deletion actually has to destroy

Deleting a `Project` or `Run` is two operations that look like one:

1. **DB rows.** Every cascade is wired (`on_delete=CASCADE`,
   [models.py](../inspect_api/models.py)): deleting a `Project` takes its
   `Dataset`s, `Artifact`s, `Shell`s, `Run`s; deleting a `Run` takes its
   `RunDataset`s and that run's `Event`s. There is simply no endpoint — adding
   `DestroyModelMixin` to `ProjectViewSet`/`RunViewSet` is a few lines. **This
   part is solved.**

2. **Bytes on disk.** Artifacts are referenced **in place** via
   `Artifact.relpath` resolved against `project.source_root` — they are *not*
   copied into a tree we own (except the zip-`/import` case and our own
   derived outputs). So a naïve "delete project → `rm -rf source_root`" would
   wipe the user's **primary PanDDA output**. This part is the design.

The mistake to avoid is treating deletion as a DB-only operation. Runs are a
*disk* operation (§3); if delete only touches rows, the disk silently
out-survives the database and bites the next analysis.

---

## §1 — File cleanup is driven off `Artifact.origin`, never the filesystem

The field that makes disk cleanup safe already exists: `Artifact.origin` ∈
`{IMPORTED, BUILT, REFINED}`. It tells us, per artifact, whether the bytes are
**ours** (we wrote them) or **the user's** (we only point at them).

| origin | bytes live where | ours to delete? |
|---|---|---|
| **IMPORTED** (in-place via `/ingest_path`) | user's external PanDDA tree | **No** — never touch on disk |
| **IMPORTED** (zip via `/import`) | copy under `PANDDA_DATA_ROOT/<name>` | Yes — but only via §2's flag |
| **BUILT** | `<root>/builds/<n>/model.pdb` ([buildservice.py:84](../inspect_api/buildservice.py#L84)) | **Yes** — derived, write-once |
| **REFINED** | `<root>/jobs/<id>/…` ([jobservice.py:251](../inspect_api/jobservice.py#L251)) | **Yes** — derived job output |
| **LIGAND** (CIF in `contents`) | the DB row itself | free — vanishes with the row |

**The rule:**

> Drive file cleanup off the `Artifact` table, never off the filesystem. For
> each artifact being deleted, delete its bytes **only if**
> `origin ∈ {BUILT, REFINED}`, resolved through the storage seam. IMPORTED
> bytes are left on disk. Embedded `contents` need no action.

This is surgical: we delete exactly the relpaths we wrote (`builds/`, `jobs/`)
and never reason about "is this whole tree mine?" That precision matters
because of a wrinkle — BUILT/REFINED write under `source_root` *when it is set*
(`_project_root`/`_job_root` return `source_root` if present, else
`PANDDA_JOBS_ROOT`). So our derived bytes can sit **nested inside the user's
in-place tree**. A whole-tree `rm` is therefore *never* safe; per-artifact
deletion is the only correct approach.

### Three pieces of missing machinery

1. **The seam is read-only.** `LocalFileStore`/`AzureBlobStore`
   ([storage.py](../inspect_api/storage.py)) resolve and read but cannot
   delete. Cleanup must go through a new `DataStore.delete(relpath)` so the
   non-local stores stay correct — do **not** re-introduce an inline
   `source_root`/`relpath` resolver (same rule as the read path; CLAUDE.md /
   Materia R6).
2. **We don't record import mode.** For the zip-`/import` case we'd also want
   to `rm` the copied tree, but nothing on `Project` says "I copied this" vs
   "I'm pointing at the user's tree." Add **`Project.source_managed: bool`**
   (true from `/import`, false from `/ingest_path`) as the gate for whether the
   source tree itself may be removed.
3. **`current_model` guard.** A BUILT/REFINED artifact may be a
   `Dataset.current_model` or `Event.current_model`. Blindly deleting one
   loses the model of record (SET_NULL nulls the pointer silently). Cleanup
   must refuse-or-warn on artifacts still referenced as a current model —
   mirroring `reconcile._apply_pointer_policy`.

### Run it as a Job, not inline

`rm`-ing `jobs/`/`builds/` trees and issuing blob deletes can be slow. Even
"hard delete + confirm" should fire the file sweep through the existing `Job`
machinery rather than blocking the request.

---

## §2 — Scope semantics: archive the project, hard-delete the run

Most of what a delete destroys is reconstructible by re-ingesting the PanDDA
tree (Events, RunDatasets, Shells, IMPORTED artifacts). Three things do **not**
survive a re-ingest:

- **`Finding`s** — the decision/confidence/comment/inspected_by. Hand-entered
  human judgement.
- **BUILT models** — ligands a human placed in Moorhen and committed.
- **REFINED outputs** — compute that took real wall-clock time.

The decision is not "delete vs not" — it is "how much ceremony around
destroying *those three*." The split that fits the data is **asymmetric**:

> **Soft-archive at the Project level; hard-delete everywhere else.**

- **Project — soft `archived` flag.** A project is the heavy, decision-dense
  container and the thing a user fat-fingers. One manager + one queryset filter
  gives undo for free; a separate explicit **purge** runs the §1 file sweep.
  Archive doubles as a feature you want anyway (finished projects clutter the
  list). *Cost:* every queryset that enumerates projects (the ViewSets, the
  `extra_roots` builder, `get_store`) must exclude archived rows or it leaks
  ghosts — easy to miss one.
- **Run + the artifact sweep — hard delete + confirm-summary.** A run is cheap
  to lose (re-ingestable; `Finding`s are kept — see §2.1). Adding soft-delete
  plumbing to the run/event/artifact layer is where the queryset-leak cost
  explodes, for little gain. The confirm step reports what's lost ("removes 14
  findings with decisions, 6 built models") — but note it is **advisory only**:
  an API/script client can `DELETE` without ever fetching the summary, so the
  real safety lives in §1 (files) and §3 (the zombie guard), not the dialog.

The §1 file-cleanup design is identical either way — archive just delays *when*
the sweep fires.

### §2.1 — Orphans on run-delete: keep them

Deleting a `Run` cascades its `RunDataset`s and that run's `Event`s, but:

- **`Finding`s survive** (run-independent, FK to `Dataset`). A `Finding` left
  anchoring no observation is **kept**, not GC'd — it is the durable human
  layer ("a decision awaiting re-observation"). A later re-ingest at the same
  detection centroid (1.5 Å, per MULTI_RUN_DATA_MODEL) re-links it. Decisions
  must never silently vanish.
- **`Dataset`s (Crystals) survive** too; one with zero remaining `RunDataset`s
  is an empty husk — kept as the crystal identity, repopulated by re-ingest.

### §2.2 — `source_root` staleness after run-delete

`project.source_root` points at the *latest* run's `out_dir`. Delete that run
and dataset-level IMPORTED artifacts (apo pdb/mtz, attached to `Dataset` not
`Run`) still resolve via `extra_roots` (built from surviving `Run.out_dir`s in
`get_store`) — until the last run is gone. Re-ingest fixes it; worth a log line
on delete, not a blocker.

---

## §3 — Run output trees & the submit-time zombie guard

This is where §1's "leave IMPORTED bytes alone" policy springs a leak. It is a
**DB↔disk desync**: deleting a project cascades the `Run` rows away, but the
analysis output tree on the share is — by §1 — *not ours to delete if it was an
in-place ingest*. **The disk remembers what the database forgot.**

### Two directories, two keys — don't conflate them

- **Analysis output tree** — `Run.out_dir`, from
  `_default_out_dir(share_path, group)`
  ([runservice.py:78-89](../inspect_api/runservice.py#L78-L89)): swaps
  `pandda_inputs`→`pandda_results`, else appends `pandda2_out`. **Keyed on
  `(share_path, group)` — not the project name.**
- **Import copy** — `PANDDA_DATA_ROOT/<project_name>` from the zip `/import`
  path, **keyed on name**, and *already* guarded (raises if the dir exists,
  [importer.py:126](../inspect_api/importer.py#L126)).

The import case is guarded; the **fresh-analysis trigger is not**.

### What happens today: silent reuse

[runservice.py:173-184](../inspect_api/runservice.py#L173-L184) checks only that
the *parent* exists, then `out_dir.mkdir(exist_ok=True)` — a pre-existing,
populated dir is written into without complaint. Three things then conspire:

1. **The dedup can't save you.** `idempotency_key`
   ([models.py:585](../inspect_api/models.py#L585)) is **DB-resident**; a
   project delete cascades the `Run` rows away, so there is no surviving key to
   hit — a re-submit creates a *fresh* `Run` believing the dir is pristine.
2. **PanDDA2 merges into the polluted tree** (no `--overwrite` is passed;
   behaviour is whatever the pinned image does into a non-empty dir). You get
   Frankenstein output — stale `pandda_analyse_events.csv` rows with no matching
   maps, ghost `processed_datasets/`, the off-by-one event/map counts CLAUDE.md
   already warns about.
3. **Ingest faithfully imports the mess.** `_complete()` runs `ingest_pandda2`
   against `out_dir` and reconcile clobbers IMPORTED artifacts wholesale, so the
   zombie's stale rows become real DB rows.

### The invariant, enforced from both ends

> **A populated `out_dir` is owned by exactly one live `Run` (or nobody).**

Enforce it at both the delete side and the submit side — defense in depth,
because zombies also arise from crashes and manual `rm` mishaps, not only
deletes.

**Delete-side — purge the trees we launched.** This refines §1's ownership rule.
A project-level `source_managed` flag is too coarse for analysis output; the
right discriminator is **`Run.out_dir` provenance**: a tree we wrote by
launching a run *is* ours to `rmtree` on delete; a tree the user pointed us at
via `/ingest_path` is not. (Do it as a Job — these trees are large.)

**Submit-side guard — never trust the dir.** Before dispatch, cross-check the
filesystem against the `Run` table:

| `out_dir` state | a live `Run` references it? | action |
|---|---|---|
| empty / absent | — | proceed (today's happy path) |
| populated | **yes** (`retry_of` / resume / idempotent re-POST) | proceed per existing logic |
| populated | **no** | **zombie → `409`**: *"`<out_dir>` already contains a PanDDA tree owned by no run — purge it or pick a new group."* |

The third row is the whole point: it is the DB↔disk reconciliation that closes
the desync. **It must consult the filesystem, not just the `Run` table** —
precisely because the idempotency key is DB-resident and is therefore
structurally incapable of seeing on-disk state once the rows are gone.

The guard keys on the `Run`↔`out_dir` *link*, not mere existence, so that the
legitimate non-empty case — `retry_of` writing into the same dir on purpose
([runservice.py:163-171](../inspect_api/runservice.py#L163-L171)) — is *not*
refused. "Populated" alone never means refuse; "populated **and** unowned" does.

### Symmetry with `/import`

After a project delete that left a `source_managed` copy in place, re-`/import`
with the same name would hit the existing `importer.py:126` guard and refuse —
the same zombie biting at a different door. Reinforces the conclusion: delete
must purge the trees it owns, and the write paths (`/runs` submit and `/import`)
must refuse to write onto an unowned tree. **Same invariant, three doors.**

---

## §4 — Materia delete-endpoint brief (2026-06-11): API shape + five corrections

Materia proposed the concrete HTTP surface that drives §1–§3. We adopt its
shape; this section records it and the five corrections found when checking it
against the actual schema.

### Adopted shape

```
DELETE /api/v1/runs/<id>?delete_outdir=<mode>      # hard-delete the run
DELETE /api/v1/projects/<id>                        # ARCHIVE (soft, reversible)
POST   /api/v1/projects/<id>/unarchive/             # restore
POST   /api/v1/projects/<id>/purge/?delete_outdirs=<mode>   # irreversible
```
`<mode>` ∈ `false` (default, DB-only, returns the on-disk path), `true`
(safe-delete with the orphan check), `force` (`rm` regardless, accept broken
pointers). Response carries an audit summary
(`{run_id, events_deleted, artifacts_deleted, disk_freed_bytes}`).

**Project shape note (vs the brief):** the brief proposed
`DELETE /projects/<id>?delete_outdirs=…` as a hard delete. Per Q2 (§2 / the
Materia reply) a project DELETE instead **archives** (reversible); the
irreversible cascade + file sweep is the explicit `POST .../purge/` step, which
**requires the project to be archived first** (else `400`). So `delete_outdirs`
lives on `purge`, not on the project `DELETE`. Materia's CLI therefore needs a
separate purge command — the one piece of Materia-side coordination this split
implies.

### Ownership predicate — `runner_handle`, confirmed

A Run's `out_dir` is Reinspect-owned **iff `run.runner_handle != ""`** AND
`run.status` is terminal (`succeeded`/`failed`/`cancelled`,
[models.py:536-542](../inspect_api/models.py#L536-L542)). This is §1's "we wrote
it" / §3's "`out_dir` provenance" made concrete with an existing field.
**Confirmed safe:** the synthetic in-place-ingest Run
([reconcile.py:211](../inspect_api/reconcile.py#L211)) never sets
`runner_handle` (stays `""`) and its `out_dir` *is* `source_root` — so the
predicate refuses to `rm` the user's own tree. (`runner_handle` is the per-Run
half; the zip-`/import` copied tree needs the separate project-level
`source_managed` gate — see correction 4.)

### Correction 1 — the orphan check queried the wrong artifact class

Dataset-scoped artifacts carry **`project = NULL`**; they reach their project
only via `dataset.project` (proven by `Artifact.owning_project`,
`self.project or (self.dataset.project if self.dataset else None)`,
[models.py:432](../inspect_api/models.py#L432)). So the brief's
`filter(event__isnull=True, project=run.project)` matches only **project-scoped
`report_html`** and never inspects a single `structure`/`data_mtz`. The orphan
query must be:

```python
Artifact.objects.filter(
    dataset__project=run.project, dataset__isnull=False, event__isnull=True
)
```

### Correction 2 — embedded ligands & symlinked inputs aren't files in `out_dir`

The brief's three-class table listed `ligand` as a disk artifact. Ligand
restraint dicts are **embedded in `Artifact.contents`**
([ingest_pandda2.py:408](../inspect_api/management/commands/ingest_pandda2.py#L408))
— CASCADE-deleted with the row, untouched by `rm_rf(out_dir)`. And the apo
`structure`/`data_mtz` inputs are **symlinks into a sibling `data/` tree**, so
`rm` removes the link, not the target. Corrected table:

| Artifact class | scope | bytes on disk under `out_dir`? | freed by |
|---|---|---|---|
| `event_map`, `ligand_pose` | event | yes | `rm_rf(out_dir)` (+ DB via Event cascade) |
| `structure`, `data_mtz` | dataset | yes, but apo inputs are **symlinks** (rm drops the link) | `rm_rf(out_dir)`; subject to the orphan check |
| `ligand` | dataset | **no — embedded in `contents`** | DB row delete |
| `report_html` | project | yes | only on `Project.delete()` |

### Correction 3 — shared `out_dir`s, and `delete_project` inverts the check

`_default_out_dir` is keyed on `(share_path, group)` and submit does
`mkdir(exist_ok=True)`, so two distinct runs (same group, different
`input_hash`) can **share one `out_dir`**. Two consequences:

- **Run-delete:** deleting run A excludes B's path from `surviving_relpaths`
  (same path) then `rm`s it — destroying B's **event-scoped** maps, which the
  orphan check never inspects (`event__isnull=True` only). Before `rm`,
  cross-check that **no surviving Run shares this exact `out_dir`**; if one does,
  refuse (or DB-delete only).
- **Project-delete:** "apply per-Run" makes surviving roots empty for every run
  ⇒ everything flags orphaned ⇒ always refuses unless `force`. Project-delete
  must use distinct semantics: all runs are going, so **skip the per-run orphan
  check and `rm` every owned `out_dir` in one pass**.

### Correction 4 — the zip-`/import` copied tree needs `source_managed`

The brief is entirely `Run.out_dir`-centric. A project from `/import` has its
tree **copied** to `PANDDA_DATA_ROOT/<name>` (importer `copytree`); its
synthetic ingest Run has `runner_handle=""` so per-Run disk delete is refused —
**leaking the copied tree forever** on `delete_project`. Gate that tree on the
project-level **`Project.source_managed`** flag (§1, missing-machinery #2):
`runner_handle` frees triggered `out_dir`s, `source_managed` frees the import
copy. Both are required.

### Correction 5 — ship the §3 submit-side guard *with* these endpoints

`delete_outdir=false` is the default, and it is a **zombie factory** for
Materia's own lead use case ("re-run PanDDA and clear the previous run's
artefacts"): DB rows go, `out_dir` stays, the next analysis `mkdir(exist_ok=True)`
merges into the stale tree → Frankenstein output that ingest then imports. The
delete endpoints and §3's submit-side guard (*refuse a populated `out_dir` no
live Run owns*) are two halves of one DB↔disk invariant and must land together.

### Answers to the brief's four open questions

1. **Authorization** — same bearer for run-delete; for project-delete keep it
   archive-not-purge (Q2) and gate the irreversible purge behind a separate
   explicit step, since project-delete is the fat-finger target.
2. **Soft vs hard** — soft-archive Projects (§2), hard-delete Runs. Tombstoning
   *runs* is unnecessary: decisions live on run-independent `Finding`s, which a
   hard run-delete already preserves (§2.1), so the audit trail is durable
   without a `status=deleted` run.
3. **Cascade transaction scope** — DB cascade in one transaction; disk deletion
   **outside** it (an `rm` can't roll back). Commit the DB delete, then run a
   best-effort disk sweep as a Job, reporting partial failures in the summary.
4. **Concurrent ingest** — a wall-clock recency window on `progress` is fragile
   (clocks lie — same failure class as DB-resident idempotency missing on-disk
   state). Refuse delete while an **ingest Job for the run is active**. The
   terminal-status guard alone won't catch it: `_complete()` ingests *after*
   `status=succeeded`, so an explicit ingest lock/flag is needed.

---

## Summary — what to build

*(All ✅ landed on `feat/delete-cleanup-endpoints`.)*

1. **`DataStore.delete(relpath)`** on the storage seam (local + azure).
2. **`Project.source_managed: bool`** (migration) — set by `/import` vs
   `/ingest_path`.
3. **File sweep**, driven off `Artifact.origin` (BUILT/REFINED only) +
   `Run.out_dir` provenance for run trees + `source_managed` copy, with a
   `current_model`-reference guard — run as a **Job**.
4. **Project delete** = soft `archived` flag + queryset filtering + a separate
   **purge** that fires the sweep.
5. **Run delete** = hard delete + confirm-summary; **keep** orphan
   `Finding`s/`Crystal`s.
6. **Submit-time zombie guard** in `runservice.submit_run` — refuse a populated
   `out_dir` that no live `Run` owns (`409`). Ships *with* item 7, not after.
7. **Delete endpoints** (§4): `DELETE /runs/<id>` and `/projects/<id>` with
   `delete_outdir(s)=false|true|force`. Ownership = `runner_handle != ""` +
   terminal status. Orphan query keyed on `dataset__project` (correction 1);
   skip the orphan check on project-delete (correction 3); shared-`out_dir`
   cross-check before `rm` (correction 3); audit summary in the response.
