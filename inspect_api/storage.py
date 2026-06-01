"""
DataStore seam — where artifact bytes live.

This is one of the two pluggable interfaces that let the *same* API serve a
laptop, a lab cluster, or a cloud deployment without the contract changing.
Only a local-filesystem implementation is provided; S3 / Azure Blob / a
CCP4Cloud- or CCP4i2-backed store would implement the same protocol.

The seam is deliberately the SINGLE place that turns an artifact reference into
bytes. Code that needs artifact bytes (the download view, the job/build
services) should go through here rather than resolving ``source_root``/
``relpath`` itself — otherwise the filesystem assumption leaks out and a
non-local store (uuid-keyed CCP4i2, object storage) can't be slotted in. See
docs/MATERIA_INTEGRATION.md R6.
"""
import os
from pathlib import Path
from typing import Protocol


class DataStore(Protocol):
    def open(self, relpath: str): ...
    def exists(self, relpath: str) -> bool: ...
    # Absolute on-disk path for tools that need a real file (refinement runners
    # feed servalcat/refmac local paths). A non-local store materialises bytes
    # to local disk and returns that path; ``local`` returns the resolved path
    # directly. ``None`` if the ref can't be made a local file.
    def local_path(self, relpath: str): ...


class LocalFileStore:
    """Serve artifacts straight from an ingested PanDDA project tree.

    ``root`` is the project ``source_root`` (the tree it was ingested from,
    possibly an in-place PanDDA output dir anywhere on disk).
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _resolve(self, relpath: str) -> Path:
        """Resolve ``relpath`` under root, preserving the download view's
        deliberate guard semantics:

        The traversal check is LEXICAL (os.path.normpath) and done BEFORE
        following symlinks — so it blocks ``..`` escapes while still serving
        PanDDA2's symlinked inputs (``<dtag>-pandda-input.pdb/.mtz`` symlink to
        a sibling ``data/`` dir whose *target* legitimately lives outside root).
        Resolving symlinks before the check would 404 every input structure.
        """
        candidate = self.root / relpath
        lexical = Path(os.path.normpath(candidate))
        if not str(lexical).startswith(str(self.root) + os.sep):
            raise ValueError("path escapes store root")
        return candidate.resolve()  # now follow symlinks to the real bytes

    def open(self, relpath: str):
        return open(self._resolve(relpath), "rb")

    def exists(self, relpath: str) -> bool:
        return self._resolve(relpath).is_file()

    def local_path(self, relpath: str):
        p = self._resolve(relpath)
        return p if p.is_file() else None


def get_store(project) -> DataStore:
    """The DataStore for a project's artifacts — the ``local`` default.

    Resolves the project's ``source_root`` (the ingested tree), falling back to
    ``PANDDA_DATA_ROOT/<name>`` for projects landed there by the zip importer.

    This is the ``local`` binding. A registry/factory keyed on a
    ``PANDDA_DATA_STORE`` selector (so a CCP4i2/object-store binding can be
    plugged out-of-tree) is deferred to the R0 spike — and gated by Q2 (whether
    Materia's artifacts are even born as relpaths). See docs/MATERIA_INTEGRATION.
    """
    from django.conf import settings

    root = project.source_root or (
        Path(settings.PANDDA_DATA_ROOT) / project.name
    )
    return LocalFileStore(Path(root))


# Future: S3FileStore, AzureBlobStore, CCP4CloudStore, CCP4i2Store — same
# protocol, selected via a registry in the R0 spike (not yet).
