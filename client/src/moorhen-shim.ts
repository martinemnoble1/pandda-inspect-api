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
  loadToCootFromMtzURL(
    url: string,
    name: string,
    columns: Record<string, unknown>
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

/** glRef recentre+zoom — not on the typed MGWebGL surface in the alpha. */
export function recentre(
  glRef: { current: unknown },
  xyz: [number, number, number],
  zoom = 0.4
) {
  const gl = glRef.current as
    | { setOriginAndZoomAnimated?: (o: number[], z: number) => void }
    | null;
  gl?.setOriginAndZoomAnimated?.([-xyz[0], -xyz[1], -xyz[2]], zoom);
}
