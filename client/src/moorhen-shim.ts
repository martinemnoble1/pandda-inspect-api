/**
 * Moorhen 1.0.0-alpha.3 still has an incomplete/loose type surface for a few
 * things we use (setActiveMap action, MoorhenMap/Molecule constructor arities,
 * contour control, glRef.setOriginAndZoomAnimated). Rather than fight the
 * alpha's .d.ts, we funnel those through thin, explicitly-typed wrappers here.
 * Tighten once the upstream types stabilise.
 *
 * NOTE on constructor signatures: ground truth is the working MoorhenPanddaApp
 * prototype (same tgz), which constructs:
 *   new MoorhenMolecule(commandCentre, store, "")
 *   new MoorhenMap(commandCentre, store)
 * i.e. (commandCentre, store[, monomerLibraryPath]) — NOT glRef.
 */
import {
  MoorhenMolecule as _Molecule,
  MoorhenMap as _Map,
  setActiveMap as _setActiveMap,
  setOrigin as _setOrigin,
  setZoom as _setZoom,
  setContourLevel as _setContourLevel,
  setMapColours as _setMapColours,
  setRequestDrawScene as _setRequestDrawScene,
  showMap as _showMap,
  hideMap as _hideMap,
} from "moorhen/react-lib";

// Loosely-typed handles. `any` is deliberate and localised to this shim.
const MoleculeCtor = _Molecule as unknown as new (
  commandCentre: unknown,
  store: unknown,
  monomerLibraryPath?: string
) => MoorhenMoleculeLike;
const MapCtor = _Map as unknown as new (
  commandCentre: unknown,
  store: unknown
) => MoorhenMapLike;

// A live representation on a molecule. `cid` is the atom selection; assigning a
// new value then calling redraw() rebuilds the buffers from it (MoorhenMolecule
// Representation.redraw reads this.cid) — how we toggle hydrogen visibility.
export interface MoorhenRepresentationLike {
  style: string;
  cid: string;
  redraw(): Promise<void>;
}
export interface MoorhenMoleculeLike {
  name: string;
  molNo: number;
  // options threads through to the internal fetch (header-based auth — see
  // api.authHeaders / the 431 note). Verified against moorhen 0.23
  // MoorhenMolecule.loadToCootFromURL(url, molName, options?: RequestInit).
  loadToCootFromURL(
    url: string,
    name: string,
    options?: RequestInit
  ): Promise<unknown>;
  // Returns the created representation (mutable cid + redraw) so callers can
  // re-perceive/redraw IT after a dict load, rather than fetchIfDirtyAndDraw
  // (which hardcodes cid "/*/*/*/*" and would add a duplicate full-atom rep,
  // re-showing hydrogens through our [!H] selection).
  addRepresentation(
    style: string,
    cid: string
  ): Promise<MoorhenRepresentationLike>;
  delete(): Promise<unknown>;
  addDict(dict: string): Promise<unknown>;
  // Re-read the molecule's atoms/bonds (after addDict) so a subsequent
  // representation redraw perceives the dictionary's bond orders. Pair with
  // rep.redraw() instead of fetchIfDirtyAndDraw to avoid the duplicate-rep bug.
  updateAtoms(): Promise<void>;
  // Add a colour rule. For a single WHOLE-MOLECULE colour (used to tint a
  // pinned sibling's pose by its source hue, A5): ruleType "molecule", cid
  // "//*", a hex colour, args ["//*", hex]. Call BEFORE addRepresentation so the
  // representation draws with it. Verified against Moorhen 0.23
  // MoorhenMolecule.addColourRule + ModifyColourRulesCard's "molecule" case.
  addColourRule(
    ruleType: string,
    cid: string,
    color: string,
    args: (string | number)[],
    isMultiColourRule?: boolean
  ): void;
  // After addDict, Coot must RE-PERCEIVE bonds so aromatic/double orders from
  // the dictionary are drawn (addDict alone doesn't redraw). Mark atoms dirty
  // then re-fetch+draw — the proven 0.23 sequence. Without this the ligand
  // renders all-single-bonds even with a correct dict.
  setAtomsDirty(state: boolean): void;
  fetchIfDirtyAndDraw(style: string): Promise<unknown>;
  // Live representations on this molecule. Each has a mutable `cid` (atom
  // selection) and `redraw()`, which rebuilds its buffers from that cid — so
  // hydrogens are hidden/shown by swapping the cid to `[!H]` / `/*/*` and
  // redrawing, the atom-level mechanism (NOT residue-level hideCid). See
  // bondsCidFor / onToggleHydrogens.
  representations: MoorhenRepresentationLike[];
  // Export the molecule's current coordinates as a string (PDB by default) —
  // used to persist a Coot-merged model back through the API (the build
  // action: merge in Coot, land the bytes server-side).
  getAtoms(format?: string): Promise<string>;
}
export interface MoorhenMapLike {
  name: string;
  molNo: number;
  contourLevel: number;
  suggestedContourLevel?: number;
  // Map RMSD (~1σ in absolute map units), populated by getSuggestedSettings
  // during load. Coot's contour API takes ABSOLUTE units, so a σ level is
  // sigma * mapRmsd.
  mapRmsd: number;
  isDifference: boolean;
  // EM / origin-lock flags. For a directly-read CCP4 map Moorhen runs is_EM_map
  // and, if true, locks the contour to the cell centre (ignoring the origin).
  // PanDDA event maps are crystallographic, so we force these off — see
  // InspectDrawer.loadEvent.
  isEM: boolean;
  isOriginLocked: boolean;
  // Map centre in Moorhen's NEGATED look-at convention, populated lazily by
  // fetchMapCentre() (Coot get_map_molecule_centre). A freshly-loaded CCP4 map
  // has this null — and MapScrollWheelListener, which mounts ONLY for the active
  // map, reads mapCentre[0] unconditionally on render. So fetchMapCentre() MUST
  // resolve a non-null centre BEFORE setActiveMap, or the render tree crashes
  // ("null is not an object"). See InspectDrawer's activation block + fb17bf4.
  mapCentre: [number, number, number] | null;
  fetchMapCentre(): Promise<[number, number, number] | null>;
  // PanDDA1 event data is an MTZ (needs FEVENT/PHEVENT column labels).
  // options threads to the internal fetch (header auth — see api.authHeaders).
  loadToCootFromMtzURL(
    url: string,
    name: string,
    columns: Record<string, unknown>,
    options?: RequestInit
  ): Promise<unknown>;
  // PanDDA2 event maps are CCP4 real-space maps (no column labels). Real 0.23
  // signature is (url, name, isDiffMap?, decompress?, options?: RequestInit)
  // (MoorhenMap.loadToCootFromMapURL) — options is the FIFTH arg, so decompress
  // must be passed (false) to reach it. options threads to the internal fetch.
  loadToCootFromMapURL(
    url: string,
    name: string,
    isDiffMap?: boolean,
    decompress?: boolean,
    options?: RequestInit
  ): Promise<unknown>;
  drawMapContour(): Promise<unknown>;
  // Recolour a NON-difference map from the mapContourSettings.mapColours slice
  // (set via setMapColours) and redraw it. Mirrors Moorhen's MapColourSelector.
  // REJECTS on a difference map (those use the positive/negative diff variants),
  // so callers must only invoke it for non-diff maps.
  fetchColourAndRedraw(): Promise<void>;
  delete(): Promise<unknown>;
}

// NB: pass the commandCentre REF object (not .current). MoorhenMolecule/Map
// read this.commandCentre.current.cootCommand internally, so they need the ref.
export function newMolecule(
  commandCentreRef: unknown,
  store: unknown
): MoorhenMoleculeLike {
  return new MoleculeCtor(commandCentreRef, store, "");
}
export function newMap(
  commandCentreRef: unknown,
  store: unknown
): MoorhenMapLike {
  return new MapCtor(commandCentreRef, store);
}

// Coot's MTZ column heuristic (auto_read_make_and_draw_maps), wrapped to take a
// URL. MoorhenMap.autoReadMtz reads the MTZ header and returns one map per
// detected coefficient set, with isDifference + the chosen F/PHI already set —
// the canonical, deterministic detection that handles every refmac-family MTZ
// (dimple 2FOFCWT, servalcat FWT/DELFWT, …). We use this for the model-map MTZ
// by default; an artifact may still pin columns explicitly to override it.
// autoReadMtz wants a File, so fetch the bytes and wrap them.
const MapStatic = _Map as unknown as {
  autoReadMtz(
    source: File,
    commandCentreRef: unknown,
    store: unknown
  ): Promise<MoorhenMapLike[]>;
};
export async function autoReadMtzFromURL(
  url: string,
  filename: string,
  commandCentreRef: unknown,
  store: unknown,
  // Forwarded to the bytes fetch so the model MTZ loads header-authenticated
  // (header — not token-in-URL — auth; see api.authHeaders / the 431 note).
  options?: RequestInit
): Promise<MoorhenMapLike[]> {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`${resp.status} fetching ${url}`);
  const buf = await resp.arrayBuffer();
  // Name must end in .mtz — autoReadMtz/MtzWrapper key off the extension.
  const name = filename.toLowerCase().endsWith(".mtz")
    ? filename
    : `${filename}.mtz`;
  const file = new File([buf], name, { type: "application/octet-stream" });
  return MapStatic.autoReadMtz(file, commandCentreRef, store);
}

export const setActiveMap = _setActiveMap as (map: unknown) => {
  type: string;
  payload: unknown;
};

/**
 * Redux action to set a map's contour level (ABSOLUTE map units, per molNo).
 * This is the authoritative path: MoorhenMapManager holds a useSelector on
 * `mapContourSettings.contourLevels` and re-contours when it changes. Mutating
 * `map.contourLevel` imperatively does NOT trigger that redraw — same Redux-vs-
 * imperative trap as the origin. Always dispatch this to change the level.
 */
export const setContourLevel = _setContourLevel as (payload: {
  molNo: number;
  contourLevel: number;
}) => { type: string; payload: unknown };

/**
 * Pin a NON-difference map's colour by molNo (rgb 0–255). Coot/Moorhen otherwise
 * colours each new map from a hue-rotating wheel (MoorhenMap.setDefaultColour,
 * +10°/map), which drifts as we churn maps across event switches. This slice is
 * read IN PREFERENCE to that default by MoorhenMap.getMapContourParams, so
 * dispatching it pins the colour and overrides the wheel. Pair with
 * `map.fetchColourAndRedraw()` then `setRequestDrawScene()` to recolour
 * immediately — same sequence as Moorhen's own MapColourSelector. (Difference
 * maps keep their fixed ±green/red default; do NOT pin them here.) See the
 * map-state refactor doc, PR A2.
 */
export const setMapColours = _setMapColours as (payload: {
  molNo: number;
  rgb: { r: number; g: number; b: number };
}) => { type: string; payload: unknown };

// Ask Moorhen to redraw the GL scene (it toggles a switch the renderer watches;
// the boolean payload is ignored). Dispatch after a colour change.
export const setRequestDrawScene = _setRequestDrawScene as (
  payload?: boolean
) => { type: string; payload: unknown };

// Map visibility is Redux-driven too (mapContourSettings.visibleMaps;
// MoorhenMapManager shows/hides reactively off it). Pass the MAP object.
export const showMap = _showMap as (
  map: unknown
) => { type: string; payload: unknown };
export const hideMap = _hideMap as (
  map: unknown
) => { type: string; payload: unknown };

/**
 * Recentre the view on a Cartesian (Ångström) coordinate.
 *
 * Moorhen's centre + contour-follow machinery is driven by the Redux
 * `glRef.origin` (stored as the NEGATED look-at point, per glRefSlice and the
 * wheel handler in baby-gru components/webMG/MoorhenWebMG.tsx, which dispatches
 * `setOrigin([-x,-y,-z])`). `MoorhenMap.drawMapContour` reads `glRef.origin`
 * straight from the store. So the authoritative move is the DISPATCH; poking
 * `glRef.current.setOrigin()` alone updates the camera but not the store, so
 * maps never re-contour at the new centre. We dispatch (source of truth) and
 * ALSO nudge the GL imperatively so the camera moves immediately even if the
 * store→GL sync effect is disabled in this build.
 */
export function recentre(
  dispatch: (action: { type: string }) => void,
  glRef: { current: unknown },
  xyz: [number, number, number],
  // Optional view zoom. Moorhen's zoom is a frustum scale (default 1.0); SMALLER
  // = closer. Event sites want a tight focus — the default 1.0 sits too far out
  // for a single binding pocket. Same dispatch-is-source-of-truth story as
  // origin: the store `zoom` drives MoorhenWebMG's setZoom effect; we also nudge
  // glRef so it moves even if that effect is disabled in this build.
  zoom?: number
) {
  const target: [number, number, number] = [-xyz[0], -xyz[1], -xyz[2]];
  dispatch(_setOrigin(target) as { type: string });
  if (typeof zoom === "number") {
    dispatch(_setZoom(zoom) as { type: string });
  }
  const gl = glRef.current as
    | {
        setOrigin?: (o: number[], doDraw?: boolean, dispatch?: boolean) => void;
        setZoom?: (z: number) => void;
        drawScene?: () => void;
      }
    | null;
  // Imperative nudge so the camera follows even if the originState useEffect is
  // disabled in this Moorhen build. doDraw=true, dispatch=false (we already did).
  if (typeof gl?.setOrigin === "function") {
    gl.setOrigin(target, true, false);
  }
  if (typeof zoom === "number" && typeof gl?.setZoom === "function") {
    gl.setZoom(zoom);
    gl.drawScene?.();
  }
}
