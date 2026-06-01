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

// The three knobs that let the backend resolve servalcat (CCP4 setup script,
// conda.sh, conda env name). `effective` is what the backend will be given;
// `detected` is the auto-discovered value; `overridden` says whether the user
// pinned each one. An empty-string override means "force off / not installed".
export interface RefineEnvKeys {
  CCP4_SETUP_SH: string;
  CONDA_SH: string;
  PANDDA2_CONDA_ENV: string;
}

export interface RefineEnvInfo {
  effective: RefineEnvKeys;
  detected: RefineEnvKeys;
  overridden: Record<keyof RefineEnvKeys, boolean>;
}

export interface PanddaDesktop {
  isDesktop: true;
  pickDirectory(opts?: {
    title?: string;
    buttonLabel?: string;
  }): Promise<string | null>;
  pickFile(opts?: {
    title?: string;
    buttonLabel?: string;
  }): Promise<string | null>;
  getDataDir(): Promise<DataDirInfo>;
  setDataDir(path: string): Promise<{ path: string; restartRequired: boolean }>;
  getRefineEnv(): Promise<RefineEnvInfo>;
  // Patch override values; a key set to "" forces off, null clears the override.
  setRefineEnv(
    patch: Partial<Record<keyof RefineEnvKeys, string | null>>,
  ): Promise<{ restartRequired: boolean }>;
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
