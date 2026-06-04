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
import io
import os
import posixpath
import tempfile
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


class AzureBlobStore:
    """Serve a project's artifacts from an Azure Blob Storage container.

    The object-storage binding for the same ``DataStore`` seam (R6). One
    container holds all projects; a project's artifacts live under the
    ``<project.name>/`` key prefix, so ``relpath`` maps to the blob key
    ``<project.name>/<relpath>`` — the direct analogue of ``source_root``.

    READ side only: this resolves an artifact reference to bytes / a local file
    for the download view. The RUN path (refinement input/output staging in
    ``jobservice``) is deliberately NOT routed here — see that module's
    ``_resolve_path`` docstring and docs/MATERIA_INTEGRATION.md (Q2-gated).

    ``azure-storage-blob`` is imported lazily so the base install (and the
    desktop app / test suite) never needs it; it ships in requirements-cloud.txt.
    Configured entirely from env, so no Azure-side decision is needed to test it
    against Azurite (the local emulator):

    * ``AZURE_STORAGE_CONNECTION_STRING`` — Azurite's well-known dev string works.
    * ``AZURE_STORAGE_CONTAINER`` — the container holding ingested artifacts.
    * ``PANDDA_BLOB_CACHE`` — optional dir for materialised blobs (default: a
      temp dir). ``local_path`` writes here for tools that need a real file.
    """

    def __init__(self, project):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "PANDDA_DATA_STORE=azure requires azure-storage-blob "
                "(pip install -r requirements-cloud.txt)"
            ) from exc

        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        container = os.environ.get("AZURE_STORAGE_CONTAINER")
        if not conn or not container:
            raise RuntimeError(
                "PANDDA_DATA_STORE=azure needs AZURE_STORAGE_CONNECTION_STRING "
                "and AZURE_STORAGE_CONTAINER"
            )
        service = BlobServiceClient.from_connection_string(conn)
        self.container = service.get_container_client(container)
        self.prefix = (project.name or "").strip("/")
        cache = os.environ.get("PANDDA_BLOB_CACHE")
        self.cache_dir = (
            Path(cache) if cache
            else Path(tempfile.gettempdir()) / "pandda-blob-cache"
        )

    def _key(self, relpath: str) -> str:
        """Blob key for ``relpath`` under this project's prefix.

        Mirrors LocalFileStore's LEXICAL guard: normalise and reject ``..``
        escapes (and absolute paths). Blobs have no symlink semantics, so there
        is no symlink-follow step — the normalised check is the whole guard.
        """
        norm = posixpath.normpath(relpath)
        if norm.startswith("..") or norm.startswith("/") or os.path.isabs(norm):
            raise ValueError("path escapes store root")
        return f"{self.prefix}/{norm}" if self.prefix else norm

    def open(self, relpath: str):
        blob = self.container.get_blob_client(self._key(relpath))
        return io.BytesIO(blob.download_blob().readall())

    def exists(self, relpath: str) -> bool:
        return self.container.get_blob_client(self._key(relpath)).exists()

    def local_path(self, relpath: str):
        key = self._key(relpath)
        blob = self.container.get_blob_client(key)
        if not blob.exists():
            return None
        # Materialise to the cache dir so callers (FileResponse, refmac) get a
        # real file. Keyed by blob name so repeat reads reuse the same path.
        dest = self.cache_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob.download_blob().readall())
        return dest


def get_store(project) -> DataStore:
    """Return the DataStore for a project's artifacts.

    Factory keyed on the ``PANDDA_DATA_STORE`` selector (the R0 registry the
    seam's design always anticipated): ``local`` (default) resolves the
    project's ``source_root`` — the ingested tree — falling back to
    ``PANDDA_DATA_ROOT/<name>`` for projects landed by the zip importer;
    ``azure`` returns an :class:`AzureBlobStore` against the configured
    container. Unset/unknown selectors fall through to ``local`` so the repo
    runs standalone with no cloud config. See docs/MATERIA_INTEGRATION.md R6.
    """
    selector = os.environ.get("PANDDA_DATA_STORE", "local").lower()
    if selector == "azure":
        return AzureBlobStore(project)

    from django.conf import settings

    root = project.source_root or (
        Path(settings.PANDDA_DATA_ROOT) / project.name
    )
    return LocalFileStore(Path(root))


# Future: S3FileStore, CCP4CloudStore, CCP4i2Store — same protocol, selected
# via the PANDDA_DATA_STORE factory above.
