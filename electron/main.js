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
