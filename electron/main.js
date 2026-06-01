// Electron main process for pandda-inspect (ROADMAP #6, the laptop binding).
//
// Responsibilities, in order:
//   1. spawn the frozen Django backend (the PyInstaller binary from
//      packaging/backend.spec), pointing its SQLite DB + data at userData
//      (the app bundle is read-only) and giving it a free port;
//   2. wait until it answers on /api/v1/projects/ (migrations done, WSGI up);
//   3. open a BrowserWindow at the backend root — the backend serves the built
//      client there, same-origin with /api and cross-origin-isolated for
//      Moorhen's WASM (see packaging/server_main.py:_SpaStaticApp);
//   4. tear the backend down when the app quits.
//
// The window loads over http://127.0.0.1 (NOT file://) on purpose: the client
// makes relative /api/v1 calls and Moorhen needs a real cross-origin-isolated
// origin with COOP/COEP headers, which only HTTP can provide.
"use strict";

const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  shell,
} = require("electron");
const { spawn } = require("node:child_process");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const fs = require("node:fs");

let backend = null;
let backendPort = 0;

// --- persisted config (tiny JSON in userData; no electron-store dep) ---------
// Only one setting so far: dataDir — where the backend writes the SQLite DB,
// refinement/job outputs, and zip-imported data. Kept deliberately minimal so
// the shell has no runtime deps to bundle. The backend reads it only at spawn
// (via PANDDA_* env), so changing it needs a relaunch.
function configPath() {
  return path.join(app.getPath("userData"), "config.json");
}

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(configPath(), "utf8"));
  } catch {
    return {};
  }
}

function writeConfig(cfg) {
  fs.writeFileSync(configPath(), JSON.stringify(cfg, null, 2), "utf8");
}

// --- refinement environment discovery (CCP4 + conda) -------------------------
// servalcat (the refine tool) lives inside a CCP4 install + a PanDDA2 conda env,
// NOT on the bare PATH. The backend's probe sources CCP4_SETUP_SH then activates
// CONDA_SH/PANDDA2_CONDA_ENV before resolving the tool (inspect_api/jobs.py). A
// Finder-launched .app has NO login-shell PATH, so we can't rely on the user's
// shell having set these up — we must discover the setup scripts ourselves and
// hand them to the backend at spawn. User-set paths (Settings) always win over
// auto-detection; "" is a meaningful override (= "force off / not installed").
function firstExisting(paths) {
  for (const p of paths) {
    try {
      if (p && fs.existsSync(p)) return p;
    } catch {
      // ignore unreadable candidates
    }
  }
  return "";
}

// Common CCP4 setup-script locations across the three desktop OSes. CCP4 names
// the POSIX script `ccp4.setup-sh`; we glob the versioned install dirs.
function detectCcp4Setup() {
  const home = app.getPath("home");
  const roots = [];
  if (process.platform === "darwin") {
    roots.push("/Applications");
  } else if (process.platform === "win32") {
    // Windows uses ccp4.setup-bat; the sh probe path doesn't apply, but keep a
    // best-effort guess for WSL-style installs.
    roots.push("C:\\");
  } else {
    roots.push("/opt", "/usr/local", home);
  }
  const candidates = [];
  for (const root of roots) {
    let entries = [];
    try {
      entries = fs.readdirSync(root);
    } catch {
      continue;
    }
    for (const name of entries) {
      if (/^ccp4(-\d+)?$/i.test(name)) {
        candidates.push(path.join(root, name, "bin", "ccp4.setup-sh"));
      }
    }
  }
  return firstExisting(candidates);
}

// conda.sh ships under <conda>/etc/profile.d/conda.sh. Check the usual installs.
function detectCondaSh() {
  const home = app.getPath("home");
  const bases = [
    process.env.CONDA_PREFIX,
    process.env.CONDA_EXE && path.join(process.env.CONDA_EXE, "..", ".."),
    path.join(home, "miniconda3"),
    path.join(home, "anaconda3"),
    path.join(home, "miniforge3"),
    path.join(home, "mambaforge"),
    "/opt/miniconda3",
    "/opt/anaconda3",
    "/opt/conda",
  ];
  return firstExisting(
    bases
      .filter(Boolean)
      .map((b) => path.join(b, "etc", "profile.d", "conda.sh"))
  );
}

// The effective refinement env vars passed to the backend: user overrides from
// config (including an explicit "" = "off") take precedence over auto-detection.
// Returns only the keys we actually want to set, so an absent/undetected one is
// simply left unset (the backend treats unset as "skip that activation").
function refineEnv() {
  const cfg = readConfig().refineEnv || {};
  const ccp4 =
    cfg.CCP4_SETUP_SH !== undefined ? cfg.CCP4_SETUP_SH : detectCcp4Setup();
  const condaSh =
    cfg.CONDA_SH !== undefined ? cfg.CONDA_SH : detectCondaSh();
  const condaEnv =
    cfg.PANDDA2_CONDA_ENV !== undefined ? cfg.PANDDA2_CONDA_ENV : "pandda2";
  const env = {};
  if (ccp4) env.CCP4_SETUP_SH = ccp4;
  if (condaSh) env.CONDA_SH = condaSh;
  // Only meaningful alongside conda.sh, but harmless to pass on its own.
  if (condaEnv) env.PANDDA2_CONDA_ENV = condaEnv;
  return env;
}

// The effective data dir: the user's choice if set + still usable, else the
// conventional per-user userData dir (always writable).
function dataDir() {
  const chosen = readConfig().dataDir;
  if (chosen) {
    try {
      fs.mkdirSync(chosen, { recursive: true });
      return chosen;
    } catch {
      // Fall through to userData if the saved dir is gone/unwritable.
    }
  }
  return app.getPath("userData");
}

// --- locating the frozen backend binary --------------------------------------
// Packaged: bundled as an extraResource under resources/ (electron-builder).
// Dev: the PyInstaller output at <repo>/dist. The binary name is fixed by the
// spec (`pandda-inspect-backend`), plus .exe on Windows.
function backendBinaryPath() {
  const exe =
    process.platform === "win32"
      ? "pandda-inspect-backend.exe"
      : "pandda-inspect-backend";
  const packaged = path.join(process.resourcesPath, "backend", exe);
  if (app.isPackaged) return packaged;
  // Dev fallbacks: onedir (dist/pandda-inspect-backend/<exe>) or onefile.
  const repoRoot = path.join(__dirname, "..");
  const candidates = [
    path.join(repoRoot, "dist", "pandda-inspect-backend", exe),
    path.join(repoRoot, "dist", exe),
  ];
  return candidates.find((p) => fs.existsSync(p)) || candidates[0];
}

// --- pick a free localhost port ----------------------------------------------
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// --- readiness probe ---------------------------------------------------------
// Poll the API (not the static root) so we only show the window once Django has
// migrated and the WSGI app answers — avoids a flash of connection-refused.
function probe(port) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: "127.0.0.1", port, path: "/api/v1/projects/", timeout: 1500 },
      (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 500);
      }
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(port, { tries = 60, delayMs = 500 } = {}) {
  for (let i = 0; i < tries; i++) {
    if (backend && backend.exitCode !== null) {
      throw new Error(
        `backend exited early (code ${backend.exitCode}) before serving`
      );
    }
    if (await probe(port)) return;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error(`backend did not become ready on port ${port} in time`);
}

// --- spawn the backend -------------------------------------------------------
async function startBackend() {
  backendPort = await freePort();
  const bin = backendBinaryPath();
  if (!fs.existsSync(bin)) {
    throw new Error(
      `backend binary not found at ${bin}. Build it with ` +
        `\`pyinstaller packaging/backend.spec --noconfirm\` (dev) — CI bundles it.`
    );
  }
  const dir = dataDir();

  backend = spawn(bin, [], {
    env: {
      ...process.env,
      // CCP4/conda setup scripts so the backend can resolve servalcat — a
      // Finder-launched app has no login-shell PATH to inherit these from.
      ...refineEnv(),
      PANDDA_PORT: String(backendPort),
      PANDDA_DB_DIR: dir,
      PANDDA_DB_PATH: path.join(dir, "db.sqlite3"),
      // Job/refinement outputs + zip-imported data are written under the data
      // dir (user-configurable; defaults to userData). NB ingest-in-place
      // projects set source_root elsewhere and are NOT written here.
      PANDDA_DATA_ROOT: path.join(dir, "data"),
      PANDDA_JOBS_ROOT: path.join(dir, "jobs"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  backend.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backend.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backend.on("exit", (code, signal) => {
    backend = null;
    if (code && code !== 0 && !app.isQuitting) {
      dialog.showErrorBox(
        "Backend stopped",
        `The pandda-inspect backend exited unexpectedly ` +
          `(code ${code}${signal ? `, signal ${signal}` : ""}).`
      );
    }
  });

  await waitForBackend(backendPort);
}

function stopBackend() {
  if (!backend) return;
  const proc = backend;
  backend = null;
  // SIGTERM lets waitress shut its threads down; force-kill as a backstop.
  proc.kill("SIGTERM");
  setTimeout(() => {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }, 2000);
}

// --- window ------------------------------------------------------------------
function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    backgroundColor: "#1e1e1e",
    title: "Reinspect",
    webPreferences: {
      // The renderer loads our own trusted localhost origin and talks to the
      // backend over HTTP (the REST contract). The ONLY native capability it
      // gets is the explicit `window.panddaDesktop` surface from preload.js
      // (native folder picker + data-dir setting) — exposed via contextBridge
      // under contextIsolation, with no Node integration.
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  win.once("ready-to-show", () => win.show());
  // Open external links (e.g. the Zenodo DOI) in the system browser, not in-app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://127.0.0.1")) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });

  win.loadURL(`http://127.0.0.1:${backendPort}/`);
  return win;
}

// --- IPC: the native surface behind window.panddaDesktop (preload.js) --------
function registerIpc() {
  // Native directory picker. Returns an absolute path or null (cancelled).
  ipcMain.handle("pandda:pick-directory", async (_evt, opts = {}) => {
    const win = BrowserWindow.getFocusedWindow();
    const res = await dialog.showOpenDialog(win, {
      title: opts.title || "Choose a folder",
      properties: ["openDirectory", "createDirectory"],
      buttonLabel: opts.buttonLabel,
    });
    if (res.canceled || res.filePaths.length === 0) return null;
    return res.filePaths[0];
  });

  // Native file picker (single file). Returns an absolute path or null. Used to
  // pick the CCP4 setup script / conda.sh for the refinement environment.
  ipcMain.handle("pandda:pick-file", async (_evt, opts = {}) => {
    const win = BrowserWindow.getFocusedWindow();
    const res = await dialog.showOpenDialog(win, {
      title: opts.title || "Choose a file",
      properties: ["openFile"],
      buttonLabel: opts.buttonLabel,
    });
    if (res.canceled || res.filePaths.length === 0) return null;
    return res.filePaths[0];
  });

  // Data-dir setting. get → effective dir; set → persist (validated writable)
  // and report that a relaunch is needed (the backend reads it only at spawn).
  ipcMain.handle("pandda:get-data-dir", () => ({
    path: dataDir(),
    isDefault: !readConfig().dataDir,
    default: app.getPath("userData"),
  }));

  ipcMain.handle("pandda:set-data-dir", (_evt, newPath) => {
    if (!newPath || typeof newPath !== "string") {
      throw new Error("data dir must be a non-empty path");
    }
    fs.mkdirSync(newPath, { recursive: true }); // throws if not creatable
    fs.accessSync(newPath, fs.constants.W_OK); // throws if not writable
    writeConfig({ ...readConfig(), dataDir: newPath });
    return { path: newPath, restartRequired: true };
  });

  // Refinement environment (CCP4 + conda) → resolves servalcat in the backend.
  // get → the effective values (user override else auto-detected) plus whether
  // each came from the user; set → persist overrides ("" = force off). Read only
  // at spawn (like dataDir), so a change reports restartRequired.
  ipcMain.handle("pandda:get-refine-env", () => {
    const cfg = readConfig().refineEnv || {};
    const detected = {
      CCP4_SETUP_SH: detectCcp4Setup(),
      CONDA_SH: detectCondaSh(),
      PANDDA2_CONDA_ENV: "pandda2",
    };
    const pick = (key) =>
      cfg[key] !== undefined ? cfg[key] : detected[key];
    return {
      effective: {
        CCP4_SETUP_SH: pick("CCP4_SETUP_SH"),
        CONDA_SH: pick("CONDA_SH"),
        PANDDA2_CONDA_ENV: pick("PANDDA2_CONDA_ENV"),
      },
      detected,
      overridden: {
        CCP4_SETUP_SH: cfg.CCP4_SETUP_SH !== undefined,
        CONDA_SH: cfg.CONDA_SH !== undefined,
        PANDDA2_CONDA_ENV: cfg.PANDDA2_CONDA_ENV !== undefined,
      },
    };
  });

  // Persist refinement-env overrides. A key present (even "") is an override; a
  // key set to null clears the override (falls back to auto-detection).
  ipcMain.handle("pandda:set-refine-env", (_evt, patch) => {
    if (!patch || typeof patch !== "object") {
      throw new Error("refine env must be an object");
    }
    const cfg = readConfig();
    const refine = { ...(cfg.refineEnv || {}) };
    for (const key of ["CCP4_SETUP_SH", "CONDA_SH", "PANDDA2_CONDA_ENV"]) {
      if (!(key in patch)) continue;
      const v = patch[key];
      if (v === null) {
        delete refine[key]; // clear override → back to auto-detect
      } else if (typeof v === "string") {
        refine[key] = v; // "" is a valid override = force off
      } else {
        throw new Error(`${key} must be a string or null`);
      }
    }
    writeConfig({ ...cfg, refineEnv: refine });
    return { restartRequired: true };
  });

  ipcMain.handle("pandda:relaunch", () => {
    app.relaunch();
    app.quit();
  });
}

// --- app lifecycle -----------------------------------------------------------
// Single instance: a second launch focuses the existing window instead of
// spawning a second backend on a second port.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const [win] = BrowserWindow.getAllWindows();
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  registerIpc();

  app.whenReady().then(async () => {
    try {
      await startBackend();
    } catch (err) {
      dialog.showErrorBox("Failed to start", String(err && err.message || err));
      app.quit();
      return;
    }
    createWindow();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    // On macOS apps typically stay alive sans windows; but our whole reason to
    // exist is the window+backend pair, so quit everywhere for a clean teardown.
    app.quit();
  });

  app.on("before-quit", () => {
    app.isQuitting = true;
    stopBackend();
  });
  // Backstops in case before-quit is skipped (e.g. hard SIGINT in dev).
  app.on("will-quit", stopBackend);
  process.on("exit", stopBackend);
}
