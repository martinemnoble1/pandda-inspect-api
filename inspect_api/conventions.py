"""
MTZ map-coefficient columns — DETECTED from the file with gemmi, not hard-coded.

We record, per MTZ artifact, the (F, PHI, isDifference) coefficient sets the
client should compute maps from. The hard lesson (a real bug) was that hard-
coding columns per *producer* is fragile: the column NAMES are chosen by the
program that wrote the file, vary by version, and PanDDA2 doesn't persist its
column-override arg anywhere we can read. So instead we open the MTZ with gemmi
(already a dependency) at land/ingest and detect the standard refmac-family
coefficient pairs straight from the file. That's ground truth — robust to
dimple (2FOFCWT/FOFCWT), servalcat (FWT/DELFWT), standalone refmac, version
drift, and column overrides alike.

The client treats a stored ``map_columns`` as an explicit override; if a file
yields none (unlabelled/non-standard), the client falls back to Coot's own
``auto_read_make_and_draw_maps`` heuristic. See the map-of-record note +
[[mtz-map-columns-by-producer]].
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# PanDDA2's coordinate convention for the screened fragment: every autobuilt
# pose and the merged model name it residue ``LIG``. Coot/Moorhen (and refmac)
# bind restraints by matching the residue name to the dict's comp_id, so the
# fragment dict's monomer MUST be ``LIG`` or it renders as bare atoms / loses
# refinement restraints. See normalize_ligand_comp + the
# ligand-comp-normalisation note.
LIGAND_COMP = "LIG"

# Known (amplitude, phase, isDifference) coefficient-name families, in priority
# order. Each program labels its 2mFo-DFc + mFo-DFc maps with one of these.
# Detection requires BOTH the amplitude and phase column to be present.
_COEFFICIENT_FAMILIES = [
    # 2mFo-DFc (direct) — NOT a difference map.
    {"F": "2FOFCWT", "PHI": "PH2FOFCWT", "isDifference": False},  # dimple/refmac
    {"F": "FWT", "PHI": "PHWT", "isDifference": False},           # servalcat
    # mFo-DFc (difference).
    {"F": "FOFCWT", "PHI": "PHFOFCWT", "isDifference": True},     # dimple/refmac
    {"F": "DELFWT", "PHI": "PHDELWT", "isDifference": True},      # servalcat
]


def detect_map_columns(mtz_path) -> list:
    """Open an MTZ with gemmi and return the standard map-coefficient sets it
    contains, as ``[{"F", "PHI", "isDifference"}, ...]``.

    Returns AT MOST one direct (2mFo-DFc) and one difference (mFo-DFc) set —
    the first family of each kind that's present — so a file carrying multiple
    aliases doesn't yield duplicate maps. Empty list if the file is unreadable
    or carries no recognised coefficients (caller may then fall back to the
    client's auto-read heuristic).
    """
    try:
        import gemmi

        mtz = gemmi.read_mtz_file(str(mtz_path))
    except Exception:
        return []
    labels = {c.label for c in mtz.columns}
    out = []
    seen_kinds = set()
    for fam in _COEFFICIENT_FAMILIES:
        kind = fam["isDifference"]
        if kind in seen_kinds:
            continue
        if fam["F"] in labels and fam["PHI"] in labels:
            out.append(dict(fam))
            seen_kinds.add(kind)
    return out


# --- Back-compat constants (a few callers/tests still import these). The
# detector above is the path of record; these document the two producers we've
# verified and seed tests without needing a real MTZ on disk. ---
DIMPLE_MAP_COLUMNS = [
    {"F": "2FOFCWT", "PHI": "PH2FOFCWT", "isDifference": False},
    {"F": "FOFCWT", "PHI": "PHFOFCWT", "isDifference": True},
]
REFMAC_MAP_COLUMNS = DIMPLE_MAP_COLUMNS  # historical alias
SERVALCAT_MAP_COLUMNS = [
    {"F": "FWT", "PHI": "PHWT", "isDifference": False},
    {"F": "DELFWT", "PHI": "PHDELWT", "isDifference": True},
]


def map_columns_for_tool(tool: str) -> list:
    """Deprecated fallback: declared columns by refine-tool name, used only if a
    gemmi detection yields nothing. servalcat/refmac family by default."""
    by_tool = {
        "servalcat": SERVALCAT_MAP_COLUMNS,
        "refmac5": SERVALCAT_MAP_COLUMNS,
        "refmac": SERVALCAT_MAP_COLUMNS,
    }
    return by_tool.get(Path(tool).name, SERVALCAT_MAP_COLUMNS)


def normalize_ligand_comp(cif_text: str, *, dtag: str | None = None) -> str:
    """Canonicalise a ligand restraint dict's monomer code to ``LIG``.

    The fragment dict from CCP4i2/AceDRG may name its component anything (e.g.
    ``DRG``), but PanDDA2 writes every pose / merged-model ligand as residue
    ``LIG`` (see :data:`LIGAND_COMP`). Coot/Moorhen and refmac bind restraints
    by matching residue name to comp_id, so a non-``LIG`` comp leaves the
    fragment rendered as bare atoms with guessed bonds and loses refinement
    restraints. We rewrite the comp at the import boundary — the one place both
    consumers (the viewer and the refinement runner) read this single embedded
    blob (jobservice writes ``Artifact.contents`` to a local CIF for refmac;
    the client ``addDict``s the same artifact).

    Scoped to THIS fragment dict's single component — the protein model and its
    co-crystallised solutes (EDO/GOL/HOH) live in a separate STRUCTURE artifact
    and are never touched. Idempotent for dicts already named ``LIG``;
    unparseable input is returned unchanged (the caller still flags it).
    """
    try:
        import gemmi
    except ImportError:  # pragma: no cover - gemmi is a hard dependency
        return cif_text
    try:
        doc = gemmi.cif.read_string(cif_text)
    except Exception:  # noqa: BLE001 - any parse failure → leave bytes as-is
        return cif_text

    code = None
    for block in doc:
        if block.name.startswith("comp_") and block.name != "comp_list":
            code = block.name[len("comp_"):]
            break
    if not code or code == LIGAND_COMP:
        return cif_text

    for block in doc:
        if block.name == f"comp_{code}":
            block.name = f"comp_{LIGAND_COMP}"
        # Rewrite every column/value that carries the comp code: the comp_list
        # id/three_letter_code, and each loop's ``.comp_id`` column.
        tags = []
        for item in block:
            if item.loop is not None:
                tags.extend(item.loop.tags)
            elif item.pair is not None:
                tags.append(item.pair[0])
        for tag in tags:
            if not (
                tag == "_chem_comp.id"
                or tag.endswith(".comp_id")
                or tag.endswith(".three_letter_code")
            ):
                continue
            col = block.find_loop(tag)
            if col:
                for i in range(len(col)):
                    if col[i] == code:
                        col[i] = LIGAND_COMP
            else:
                val = block.find_value(tag)
                if val is not None and val.strip() == code:
                    block.set_pair(tag, LIGAND_COMP)

    logger.info(
        "normalised ligand comp '%s' -> '%s'%s (for restraint binding)",
        code, LIGAND_COMP, f" [{dtag}]" if dtag else "",
    )
    return doc.as_string()
