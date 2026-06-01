"""
Ligand build = LAND a client-merged crystal model through the contract.

The merge itself happens CLIENT-SIDE in Moorhen/Coot (merge_molecules — the
prototype recipe, the right layer; the ligand is placed/fitted in live
density). This module is the *persistence* side: it receives the merged PDB
bytes the client exports and records them, killing the prototype's drift bug
(it merged in Coot but never wrote the canonical record). See the
ligand-merge-client-side + legacy-inspect-model-of-record notes.

A *build* is interactive, not a dispatched job (DESIGN §2.2): register an
``Artifact(origin=built, parent=<prev model>, produced_by=null)`` repointing
the per-crystal ``Dataset.current_model``; flag the event ``pose_merged=True``.
Write-once + versioned, mirroring the refine path: each build writes a new
``builds/<n>/model.pdb`` under the project root; the pointer moves, prior
models are never mutated (the DB lineage replaces legacy's symlink-to-latest).
"""
from pathlib import Path

from django.conf import settings
from django.db import transaction

from .models import Artifact, Dataset, Event


class BuildError(Exception):
    """A built model could not be landed (empty/invalid submitted PDB)."""


def _project_root(dataset: Dataset) -> Path:
    root = dataset.project.source_root
    return Path(root) if root else Path(settings.PANDDA_JOBS_ROOT)


def _next_build_dir(dataset: Dataset) -> tuple[Path, str]:
    """A fresh ``builds/<n>/`` under the project root (write-once, no reuse).

    Returns (absolute_dir, relpath_prefix). ``n`` is one past the highest
    existing builds/<n> for this dataset, so models never clobber.
    """
    base = _project_root(dataset) / "builds"
    existing = [
        int(p.name)
        for p in (base.glob("*") if base.is_dir() else [])
        if p.is_dir() and p.name.isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    out = base / str(n)
    out.mkdir(parents=True, exist_ok=True)
    return out, f"builds/{n}"


@transaction.atomic
def land_built_model(event: Event, pdb_text: str) -> Artifact:
    """Persist a client-merged crystal model for ``event``'s dataset.

    ``pdb_text`` is the merged model the client exported from Coot (after
    merge_molecules). We version + write it, register an ``origin=built``
    Artifact (lineage parent = the model that was merged into), repoint
    ``Dataset.current_model``, and flag the event ``pose_merged=True``.
    """
    has_atoms = "ATOM" in pdb_text or "HETATM" in pdb_text
    if not pdb_text or not has_atoms:
        raise BuildError("no atoms in the submitted model")

    dataset = event.dataset
    # Lineage parent: the model that was merged into (current best, else the
    # imported structure). Records what this build derived from.
    parent = dataset.current_model or dataset.artifacts.filter(
        kind=Artifact.Kind.STRUCTURE
    ).order_by("id").first()

    out_dir, relprefix = _next_build_dir(dataset)
    (out_dir / "model.pdb").write_text(pdb_text, encoding="utf-8")

    built = Artifact.objects.create(
        project=dataset.project,
        dataset=dataset,
        kind=Artifact.Kind.STRUCTURE,
        relpath=f"{relprefix}/model.pdb",
        origin=Artifact.Origin.BUILT,
        parent=parent,
    )
    dataset.current_model = built
    dataset.save(update_fields=["current_model"])
    # This event's ligand is now in the model.
    event.pose_merged = True
    event.save(update_fields=["pose_merged"])
    return built
