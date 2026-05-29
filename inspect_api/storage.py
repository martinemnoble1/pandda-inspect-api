"""
DataStore seam — where artifact bytes live.

This is one of the two pluggable interfaces that let the *same* API serve a
laptop, a lab cluster, or a cloud deployment without the contract changing.
Only a local-filesystem implementation is provided; S3 / Azure Blob / a
CCP4Cloud-backed store would implement the same protocol.
"""
from pathlib import Path
from typing import Protocol


class DataStore(Protocol):
    def open(self, relpath: str): ...
    def exists(self, relpath: str) -> bool: ...


class LocalFileStore:
    """Serve artifacts straight from an ingested PanDDA project tree."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def open(self, relpath: str):
        return open(self._resolve(relpath), "rb")

    def exists(self, relpath: str) -> bool:
        return self._resolve(relpath).is_file()

    def _resolve(self, relpath: str) -> Path:
        path = (self.root / relpath).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("path escapes store root")
        return path


# Future: S3FileStore, AzureBlobStore, CCP4CloudStore — same protocol.
