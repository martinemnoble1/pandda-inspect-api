/**
 * Moorhen 0.23.1-alpha.0 has an incomplete/loose type surface for a few things
 * we use (setActiveMap action, MoorhenMap/Molecule constructor arities, contour
 * control, glRef.setOriginAndZoomAnimated). Rather than fight the alpha's .d.ts,
 * we funnel those through thin, explicitly-typed wrappers here. Tighten once the
 * upstream types stabilise.
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
  showMap as _showMap,
  hideMap as _hideMap,
} from "moorhen";

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
  addRepresentation(style: string, cid: string): Promise<unknown>;
  delete(): Promise<unknown>;
  addDict(dict: string): Promise<unknown>;
  // After addDict, Coot must RE-PERCEIVE bonds so aromatic/double orders from
  // the dictionary are drawn (addDict alone doesn't redraw). Mark atoms dirty
  // then re-fetch+draw — the proven 0.23 sequence. Without this the ligand
  // renders all-single-bonds even with a correct dict.
  setAtomsDirty(state: boolean): void;
  fetchIfDirtyAndDraw(style: string): Promise<unknown>;
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
