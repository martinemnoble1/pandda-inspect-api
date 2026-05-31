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
  setContourLevel as _setContourLevel,
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
  loadToCootFromURL(url: string, name: string): Promise<unknown>;
  addRepresentation(style: string, cid: string): Promise<unknown>;
  delete(): Promise<unknown>;
  addDict(dict: string): Promise<unknown>;
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
  // PanDDA1 event data is an MTZ (needs FEVENT/PHEVENT column labels).
  loadToCootFromMtzURL(
    url: string,
    name: string,
    columns: Record<string, unknown>
  ): Promise<unknown>;
  // PanDDA2 event maps are CCP4 real-space maps (no column labels). Signature
  // verified against moorhen 0.23 moorhen.d.ts:2710.
  loadToCootFromMapURL(
    url: string,
    name: string,
    isDiffMap?: boolean
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
  xyz: [number, number, number]
) {
  const target: [number, number, number] = [-xyz[0], -xyz[1], -xyz[2]];
  dispatch(_setOrigin(target) as { type: string });
  const gl = glRef.current as
    | {
        setOrigin?: (o: number[], doDraw?: boolean, dispatch?: boolean) => void;
      }
    | null;
  // Imperative nudge so the camera follows even if the originState useEffect is
  // disabled in this Moorhen build. doDraw=true, dispatch=false (we already did).
  if (typeof gl?.setOrigin === "function") {
    gl.setOrigin(target, true, false);
  }
}
