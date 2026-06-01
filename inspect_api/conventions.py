"""
Explicit MTZ map-coefficient column conventions, keyed by refinement engine.

We DECLARE these rather than rely on Coot's column heuristics — we own the
convention end-to-end (the contract-first choice), and because each refinement
executor is a swappable seam, each can name its own output columns here. The
client computes the 2mFo-DFc (direct) + mFo-DFc (difference) maps with these
exact labels. See the map-of-record note.

REFMAC covers dimple (dimple's engine IS refmac, so its
``<dtag>-pandda-input.mtz`` columns conform — verified 2FOFCWT/PH2FOFCWT +
FOFCWT/PHFOFCWT) and servalcat (refmac-backed here). Add entries for other
engines (phenix, …) as their executors are wired.
"""

REFMAC_MAP_COLUMNS = [
    {"F": "2FOFCWT", "PHI": "PH2FOFCWT", "isDifference": False},
    {"F": "FOFCWT", "PHI": "PHFOFCWT", "isDifference": True},
]

# Map-column convention per refinement tool name (matched on the trailing
# component of the tool, as the runner does). Fallback = REFMAC family.
MAP_COLUMNS_BY_TOOL = {
    "servalcat": REFMAC_MAP_COLUMNS,
    "refmac5": REFMAC_MAP_COLUMNS,
    "refmac": REFMAC_MAP_COLUMNS,
}


def map_columns_for_tool(tool: str) -> list:
    """Declared map columns for a refinement tool; REFMAC family by default."""
    from pathlib import Path

    return MAP_COLUMNS_BY_TOOL.get(Path(tool).name, REFMAC_MAP_COLUMNS)
