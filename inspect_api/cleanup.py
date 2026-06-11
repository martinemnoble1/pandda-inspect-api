"""
Deletion & cleanup service — tear down Runs (and, later, Projects) and the
files behind them.

The hard part is never the DB rows (the cascades are wired); it is deciding
which *bytes* are ours to remove. Two predicates settle it:

  * ``Run.runner_handle`` — non-empty iff Reinspect's JobRunner dispatched the
    run, so we wrote its ``out_dir``. The synthetic in-place-ingest Run leaves
    it empty and sets ``out_dir == source_root`` (the user's own tree), so the
    predicate refuses to remove a tree we don't own.
  * ``Artifact.origin`` / ``Artifact.contents`` — which artifact bytes live on
    disk under a run tree vs. embedded in the DB.

See docs/DELETION_AND_CLEANUP.md (§3 ownership, §4 the corrections this
encodes). DB delete happens first (autocommit); the disk sweep runs after and
is best-effort — a failed ``rm`` is reported, never rolled back into a
half-deleted DB.
"""
import os
import shutil
from pathlib import Path

from django.conf import settings

from .models import Artifact, Event, Finding, Run
from .storage import get_store

# A run is settled (safe to reason about its tree) only in these states; never
# touch the tree of a run the node may still be writing.
TERMINAL_STATUSES = frozenset({
    Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELLED,
})


class CleanupError(Exception):
    """A delete was refused (not owned, still running, or would orphan/clobber
    surviving artifacts). Maps to HTTP 400."""


def run_owns_outdir(run: Run) -> bool:
    """True iff ``run.out_dir`` is a tree Reinspect created and may remove:
    we dispatched it (``runner_handle`` set) AND it is terminal."""
    return bool(run.runner_handle) and run.status in TERMINAL_STATUSES


def _norm(p) -> str:
    return os.path.normpath(str(p))


def _surviving_roots(project, *, exclude_out_dir: str) -> list[Path]:
    """Candidate trees that will STILL exist after the delete — every other
    run's ``out_dir``, the project ``source_root``, and the data-root landing
    dir — minus the tree being removed. Mirrors storage.get_store's root set so
    the orphan check sees exactly what artifact serving would resolve against.
    """
    exclude = _norm(exclude_out_dir)
    roots = set()
    if project.source_root:
        roots.add(_norm(project.source_root))
    roots.update(
        _norm(od) for od in Run.objects.filter(project=project)
        .exclude(out_dir="").values_list("out_dir", flat=True)
    )
    roots.add(_norm(Path(settings.PANDDA_DATA_ROOT) / project.name))
    roots.discard(exclude)
    return [Path(r) for r in roots if Path(r).is_dir()]


def _orphaned_dataset_artifacts(run: Run) -> list[str]:
    """Dataset-scoped artifacts (structure/data_mtz) that would be left with no
    on-disk copy if ``run.out_dir`` is removed.

    Correct scope (DELETION_AND_CLEANUP.md §4 correction 1): dataset-scoped
    artifacts carry ``project=NULL`` and reach the project via ``dataset``, so
    we filter on ``dataset__project``, NOT ``project``. Embedded artifacts
    (ligand CIFs in ``contents``) are excluded (correction 2 — they are not
    files). We check existence per-artifact across the surviving roots rather
    than walking the (huge) trees to union all relpaths: dataset-scoped
    artifacts are few, so O(artifacts x roots) stats beats O(files).
    """
    survivors = _surviving_roots(run.project, exclude_out_dir=run.out_dir)
    arts = Artifact.objects.filter(
        dataset__project=run.project,
        dataset__isnull=False,
        event__isnull=True,
        contents="",
    ).values_list("relpath", flat=True)
    orphaned = []
    for relpath in arts:
        on_disk = any(
            (root / relpath).is_file() or (root / relpath).is_symlink()
            for root in survivors
        )
        if not on_disk:
            orphaned.append(relpath)
    return orphaned


def _may_rm_outdir(run: Run, *, force: bool) -> tuple[bool, str]:
    """Decide whether ``run.out_dir`` may be removed. ``force`` skips every
    guard (accepts broken pointers). Returns ``(ok, refusal_reason)``."""
    if force:
        return True, ""
    if not run_owns_outdir(run):
        return False, (
            "out_dir is not Reinspect-owned (no runner_handle) or the run is "
            "not in a terminal state — pass force to override"
        )
    # Shared out_dir: another run reads/writes the SAME tree (same
    # share_path+group, different input_hash → identical _default_out_dir).
    # Removing it would nuke that run's event-scoped maps, which the orphan
    # check never inspects. Refuse. (§4 correction 3.)
    sharers = Run.objects.filter(
        project=run.project, out_dir=run.out_dir
    ).exclude(pk=run.pk)
    if run.out_dir and sharers.exists():
        return False, (
            f"out_dir {run.out_dir} is shared with {sharers.count()} other "
            "run(s); refusing to remove it — pass force to override"
        )
    orphaned = _orphaned_dataset_artifacts(run)
    if orphaned:
        return False, (
            f"removing out_dir would orphan {len(orphaned)} dataset-scoped "
            "artifact(s) with no surviving copy — pass force to override"
        )
    return True, ""


def _tree_size(path: str) -> int:
    """Sum of regular-file sizes under ``path`` (symlinks counted as the link,
    not the target — we never follow out of the tree)."""
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            fp = Path(dirpath) / name
            try:
                total += fp.lstat().st_size
            except OSError:
                pass
    return total


def _rm_tree(path: str) -> tuple[bool, str]:
    """Best-effort recursive remove. Returns ``(removed, error)``."""
    p = Path(path)
    if not p.is_dir():
        return False, ""
    try:
        shutil.rmtree(p)
        return True, ""
    except OSError as exc:  # surfaced in the summary, never re-raised
        return False, str(exc)


def delete_run(run: Run, *, delete_outdir: bool = False,
               force: bool = False) -> dict:
    """Delete a Run (DB cascade) and, optionally, its output tree.

    ``delete_outdir=False`` (default): DB-only; the on-disk ``out_dir`` is
    left and returned so the caller can clean it up manually.
    ``delete_outdir=True``: safe-delete — refuses (CleanupError → 400) if the
    tree is not ours, is shared, or would orphan dataset-scoped artifacts.
    ``force=True``: remove regardless, accepting broken pointers.

    Findings/Crystals are intentionally NOT touched: a Finding left anchoring
    no observation is the durable human layer, kept for re-link on re-ingest
    (DELETION_AND_CLEANUP.md §2.1).
    """
    run_id = run.id
    out_dir = run.out_dir
    # Snapshot counts BEFORE the cascade removes the rows.
    events_deleted = Event.objects.filter(run_dataset__run=run).count()
    artifacts_deleted = Artifact.objects.filter(
        event__run_dataset__run=run
    ).count()

    rm_outdir = False
    if delete_outdir:
        rm_outdir, reason = _may_rm_outdir(run, force=force)
        if not rm_outdir:
            raise CleanupError(reason)

    disk_freed = _tree_size(out_dir) if (rm_outdir and out_dir) else 0

    run.delete()  # CASCADE: RunDataset → this run's Events → event artifacts

    out_dir_removed, rm_error = (False, "")
    if rm_outdir and out_dir:
        out_dir_removed, rm_error = _rm_tree(out_dir)
        if not out_dir_removed:
            disk_freed = 0

    summary = {
        "run_id": run_id,
        "events_deleted": events_deleted,
        "artifacts_deleted": artifacts_deleted,
        "disk_freed_bytes": disk_freed,
        "out_dir": out_dir,
        "out_dir_removed": out_dir_removed,
    }
    if rm_error:
        summary["out_dir_error"] = rm_error
    return summary


# --- Project archive / purge --------------------------------------------------

def project_loss_summary(project) -> dict:
    """Counts of what a PURGE would irreversibly destroy — the confirm summary
    surfaced at archive AND purge time. The three reconstructible-only-by-hand
    classes (decisions, built, refined) are called out separately."""
    events = Event.objects.filter(dataset__project=project)
    decided = Finding.objects.filter(dataset__project=project).exclude(
        decision=Event.Decision.UNREVIEWED
    )
    return {
        "n_runs": project.runs.count(),
        "n_datasets": project.datasets.count(),
        "n_events": events.count(),
        "n_findings_with_decisions": decided.count(),
        "n_built_models": Artifact.objects.filter(
            dataset__project=project, origin=Artifact.Origin.BUILT
        ).count(),
        "n_refined_models": Artifact.objects.filter(
            dataset__project=project, origin=Artifact.Origin.REFINED
        ).count(),
    }


def archive_project(project) -> dict:
    """Soft-delete: tombstone the project (reversible). Retains every row and
    byte; a later purge does the irreversible work. Idempotent."""
    if not project.archived:
        project.archived = True
        project.save(update_fields=["archived"])
    return {"id": project.id, "archived": True, **project_loss_summary(project)}


def purge_project(project, *, delete_outdirs: bool = False,
                  force: bool = False) -> dict:
    """Hard-delete a project (DB cascade) and, optionally, its files.

    Whole-project teardown, so — unlike run-delete — there is NO per-run orphan
    check (every artifact is going) and no current_model guard (every pointer
    is going). Disk removal still respects ownership: with ``delete_outdirs``
    we rm only runner-owned ``out_dir`` trees and (if ``source_managed``) the
    copied source tree, leaving a user's in-place-ingested tree untouched.
    ``force`` removes EVERY out_dir and the source tree regardless — an
    explicit nuke that can reach a user's own data. (§4 correction 3 + 4.)
    """
    loss = project_loss_summary(project)
    store = get_store(project)  # capture before the cascade removes the runs

    trees: set[str] = set()
    derived: list[str] = []
    source_removed = False
    if delete_outdirs:
        for run in project.runs.exclude(out_dir=""):
            if force or run_owns_outdir(run):
                trees.add(_norm(run.out_dir))
        if (project.source_managed or force) and project.source_root:
            trees.add(_norm(project.source_root))
        derived = list(
            Artifact.objects.filter(
                dataset__project=project,
                origin__in=(Artifact.Origin.BUILT, Artifact.Origin.REFINED),
            ).values_list("relpath", flat=True)
        )

    source_norm = _norm(project.source_root) if project.source_root else None
    disk_freed = sum(_tree_size(t) for t in trees)
    project_name = project.name

    project.delete()  # DB cascade: runs, datasets, events, findings, artifacts

    errors: list[str] = []
    trees_removed = 0
    if delete_outdirs:
        for tree in trees:
            ok, err = _rm_tree(tree)
            if ok:
                trees_removed += 1
                if tree == source_norm:
                    source_removed = True
            else:
                if err:
                    errors.append(err)
                disk_freed -= 0  # size already counted; rm failed → leave it
        # Belt-and-suspenders: sweep any BUILT/REFINED bytes that lived outside
        # a removed tree (e.g. under PANDDA_JOBS_ROOT). store paths were
        # captured before the cascade, so this still resolves.
        for relpath in derived:
            try:
                store.delete(relpath)
            except (OSError, ValueError):
                pass

    summary = {
        "project": project_name,
        **loss,
        "trees_removed": trees_removed,
        "disk_freed_bytes": disk_freed if delete_outdirs else 0,
        "source_tree_removed": source_removed,
    }
    if errors:
        summary["errors"] = errors
    return summary
