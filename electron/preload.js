// Preload: the ONLY bridge between the sandboxed renderer (the client, loaded
// over http://127.0.0.1) and Electron's native capabilities. Runs with
// contextIsolation, so it exposes a minimal, explicit surface on
// `window.panddaDesktop` via contextBridge — never the raw ipcRenderer or Node.
//
// The client feature-detects `window.panddaDesktop`: present ⇒ desktop build
// (offer the native folder picker + data-dir setting); absent ⇒ plain browser
// (zip-upload only). Nothing here trusts renderer input beyond passing it to
// main, which owns the actual dialogs and disk writes.
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("panddaDesktop", {
  // Marker the client checks to know it's running in the desktop shell.
  isDesktop: true,

  // Native directory picker → resolves to an absolute path string, or null if
  // the user cancelled. Used to feed POST /api/v1/projects/ingest_path/.
  pickDirectory: (opts) => ipcRenderer.invoke("pandda:pick-directory", opts),

  // Where novel artifacts (refinement outputs, zip imports, the DB) are
  // written. getDataDir → current path; setDataDir(path) persists it and
  // returns { path, restartRequired } (the backend reads it only at spawn).
  getDataDir: () => ipcRenderer.invoke("pandda:get-data-dir"),
  setDataDir: (path) => ipcRenderer.invoke("pandda:set-data-dir", path),

  // Restart the app so a changed data dir takes effect (re-spawns the backend).
  relaunch: () => ipcRenderer.invoke("pandda:relaunch"),
});
