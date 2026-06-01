"""
Explicit MTZ map-coefficient column conventions, keyed by the PRODUCER.

We DECLARE these rather than rely on Coot's column heuristics — we own the
convention end-to-end (the contract-first choice), and because each producer is
a swappable seam, each can name its own output columns here. The client computes
the 2mFo-DFc (direct) + mFo-DFc (difference) maps with these exact labels. See
the map-of-record note.

KEY POINT: the column NAMES are set by the program that WROTE the file, not by
the underlying engine. Both our producers are refmac-backed, yet label
differently — so we key on the producer, NOT the engine:

* DIMPLE (the map source at ingest) — its ``<dtag>-pandda-input.mtz`` carries
  the classic Coot-friendly refmac names: ``2FOFCWT/PH2FOFCWT`` (2mFo-DFc) +
  ``FOFCWT/PHFOFCWT`` (mFo-DFc). Verified: columns include 2FOFCWT PH2FOFCWT
  FOFCWT PHFOFCWT.
* servalcat (the map source after a refine) — modern gemmi-based servalcat
  writes its own native names: ``FWT/PHWT`` (2mFo-DFc) + ``DELFWT/PHDELWT``
  (mFo-DFc). Verified against a servalcat refine.mtz (FP SIGFP FOM FWT PHWT
  DELFWT PHDELWT FC …). Declaring the dimple names here made the client's MTZ
  load throw ("column missing"), silently emptying the map panel after refine.

NB a bare/standalone refmac5 also writes FWT/DELFWT — so the dimple convention
is dimple's relabelling, not "refmac's". We only produce MTZs via dimple
(ingest) and servalcat (refine) today; add entries (phenix, standalone refmac,
…) keyed on the actual producer as their executors are wired.
"""

# Dimple-produced map columns (the model-map MTZ at ingest).
DIMPLE_MAP_COLUMNS = [
    {"F": "2FOFCWT", "PHI": "PH2FOFCWT", "isDifference": False},
    {"F": "FOFCWT", "PHI": "PHFOFCWT", "isDifference": True},
]
# Back-compat alias (older imports referenced REFMAC_MAP_COLUMNS).
REFMAC_MAP_COLUMNS = DIMPLE_MAP_COLUMNS

# servalcat-produced map columns (the model-map MTZ after a refine). Also what
# a standalone refmac5 emits.
SERVALCAT_MAP_COLUMNS = [
    {"F": "FWT", "PHI": "PHWT", "isDifference": False},
    {"F": "DELFWT", "PHI": "PHDELWT", "isDifference": True},
]

# Map-column convention per refinement tool name (matched on the trailing
# component of the tool, as the runner does). servalcat is our only refine
# producer today; fall back to its names (FWT/DELFWT) since a standalone refmac
# writes those too.
MAP_COLUMNS_BY_TOOL = {
    "servalcat": SERVALCAT_MAP_COLUMNS,
    "refmac5": SERVALCAT_MAP_COLUMNS,
    "refmac": SERVALCAT_MAP_COLUMNS,
}


def map_columns_for_tool(tool: str) -> list:
    """Declared map columns for a refine tool; servalcat/refmac names default."""
    from pathlib import Path

    return MAP_COLUMNS_BY_TOOL.get(
        Path(tool).name, SERVALCAT_MAP_COLUMNS
    )
