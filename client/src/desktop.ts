// Typed access to the Electron desktop bridge (electron/preload.js).
//
// In a plain browser `window.panddaDesktop` is undefined, so every consumer
// must feature-detect via `desktop()` — the same code runs in both the web
// app and the desktop shell, lighting up native affordances (folder picker,
// data-dir setting) only when they exist.

export interface DataDirInfo {
  path: string;
  isDefault: boolean;
  default: string;
}

export interface PanddaDesktop {
  isDesktop: true;
  pickDirectory(opts?: {
    title?: string;
    buttonLabel?: string;
  }): Promise<string | null>;
  getDataDir(): Promise<DataDirInfo>;
  setDataDir(path: string): Promise<{ path: string; restartRequired: boolean }>;
  relaunch(): Promise<void>;
}

declare global {
  interface Window {
    panddaDesktop?: PanddaDesktop;
  }
}

/** The desktop bridge, or null when running in a plain browser. */
export function desktop(): PanddaDesktop | null {
  return typeof window !== "undefined" && window.panddaDesktop
    ? window.panddaDesktop
    : null;
}

export const isDesktop = (): boolean => desktop() !== null;
