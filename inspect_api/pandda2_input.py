"""Reader for a PanDDA2 run's top-level ``input.yaml`` manifest.

PanDDA2 writes ``<out_dir>/input.yaml`` (sibling to ``processed_datasets/`` and
``analyses/``): an authoritative, per-dataset map of the **real** input file
paths — the very targets that the ``processed_datasets/<dtag>/<dtag>-pandda-
input.*`` symlinks point at — plus typed ligand-spec slots, resolution and unit
cell. Shape (abridged)::

    Datasets:
      xtal-0001:
        Files:
          PDB: /data/xtal-0001/final.pdb
          MTZ: /data/xtal-0001/final.mtz
          Ligand Files:
            dict:   {PDB: None, CIF: /data/xtal-0001/dict.cif, SMILES: None}
            ligand: {PDB: /data/xtal-0001/ligand.pdb, CIF: None, SMILES: None}
        Reflections:
          Resolution: 1.69
          Unit Cell: {a: …, b: …, …}

Why prefer it over the in-tree symlinks + ``ligand_files/`` globbing the reader
historically used: it is exact (no regex/priority guessing) AND it does not
depend on the filesystem preserving POSIX symlinks. Azure Files / CIFS mounts
store a symlink as a 1067-byte "XSym" stub unless mounted ``mfsymlinks``; a
reader that ``resolve()``s the symlink (or serves it) then gets garbage. This
manifest gives the real path directly, so ingest can identify — and, where the
original data is reachable, materialise — the true bytes regardless of how the
share handled the link. See docs/CLOUD_DEPLOYMENT.md (symlink note).

Pure parsing only — no Django, no I/O beyond reading the one file. ``None`` in
the YAML is PanDDA2's Python ``repr`` (a string, not a YAML null), so every slot
is normalised through :func:`_clean`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# A CIFS ``mfsymlinks`` symlink is stored as a regular file of EXACTLY this many
# bytes, beginning with this magic — "XSym\n<len>\n<md5>\n<target padded>".
_XSYM_SIZE = 1067
_XSYM_MAGIC = b"XSym"


@dataclass
class DatasetInput:
    """The real input paths for one dataset, lifted from ``input.yaml``."""

    dtag: str
    pdb: str | None = None          # Files.PDB — the apo input coords
    mtz: str | None = None          # Files.MTZ — the input reflections
    dict_cif: str | None = None     # Ligand Files.dict.CIF — restraint dict
    ligand_pdb: str | None = None   # Ligand Files.ligand.PDB — ligand coords
    smiles: str | None = None       # Ligand Files.{dict,ligand}.SMILES
    resolution: float | None = None  # Reflections.Resolution

    @property
    def ligand_source(self) -> str:
        """Best-available ligand-spec slot (cif > pdb > smiles > none), read
        from the manifest's typed slots — PanDDA2's own LigandFiles priority,
        without touching the disk."""
        if self.dict_cif:
            return "cif"
        if self.ligand_pdb:
            return "pdb"
        if self.smiles:
            return "smiles"
        return "none"


def _clean(v) -> str | None:
    """Normalise a manifest scalar: PanDDA2 writes absent slots as the literal
    string ``None`` (its Python repr, not a YAML null), so coerce that — and
    blanks — to real ``None``; otherwise return the stripped string."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "None", "null", "~") else s


def _f(v) -> float | None:
    s = _clean(v)
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load_input_yaml(root: Path) -> dict[str, DatasetInput]:
    """Parse ``<root>/input.yaml`` → ``{dtag: DatasetInput}``.

    Returns ``{}`` when the file is absent or unparseable (older PanDDA2 trees,
    PanDDA1) — the caller falls back to symlink/glob discovery, so a missing
    manifest is never fatal.
    """
    import yaml

    ypath = Path(root) / "input.yaml"
    if not ypath.is_file():
        return {}
    try:
        data = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}
    datasets = data.get("Datasets") if isinstance(data, dict) else None
    if not isinstance(datasets, dict):
        return {}

    out: dict[str, DatasetInput] = {}
    for dtag, block in datasets.items():
        if not isinstance(block, dict):
            continue
        files = block.get("Files") or {}
        ligand = files.get("Ligand Files") or {}
        dict_slot = ligand.get("dict") or {}
        ligand_slot = ligand.get("ligand") or {}
        # SMILES may live under either typed slot; take the first present.
        smiles = _clean(dict_slot.get("SMILES")) or _clean(
            ligand_slot.get("SMILES")
        )
        refl = block.get("Reflections") or {}
        out[str(dtag)] = DatasetInput(
            dtag=str(dtag),
            pdb=_clean(files.get("PDB")),
            mtz=_clean(files.get("MTZ")),
            dict_cif=_clean(dict_slot.get("CIF")),
            ligand_pdb=_clean(ligand_slot.get("PDB")),
            smiles=smiles,
            resolution=_f(refl.get("Resolution")),
        )
    return out


def is_xsym_stub(path: Path) -> bool:
    """True if ``path`` is a CIFS ``mfsymlinks`` symlink stub — a regular file
    of exactly 1067 bytes whose content begins ``XSym``. Such a file is a
    symlink the share failed to honour as a POSIX link; reading it yields the
    target path text, not the intended bytes (the "File not identified as MTZ"
    class of failure). Cheap: a stat plus a 4-byte read."""
    try:
        if not path.is_file() or path.stat().st_size != _XSYM_SIZE:
            return False
        with open(path, "rb") as fh:
            return fh.read(len(_XSYM_MAGIC)) == _XSYM_MAGIC
    except OSError:
        return False
