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
from pathlib import Path

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
