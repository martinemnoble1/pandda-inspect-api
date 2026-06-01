# packaging — freezing the backend (ROADMAP #6, Electron handover)

The laptop binding of the contract architecture: the Django backend + SQLite +
(later) the built client, frozen into one standalone binary that Electron's main
process spawns. Same backend, same contract as the hosted/compose deployments —
only the `DataStore`/`JobRunner` bindings differ.

## Files

- `server_main.py` — standalone entry. Bootstraps `sys.path` (works from source
  and from a PyInstaller bundle via `sys._MEIPASS`), sets the settings module,
  points SQLite at a **per-user writable dir** (the bundle is read-only), runs
  migrations, and serves the WSGI app with **waitress** (pure-Python; freezes
  cleanly, no fork/exec). `--selfcheck` proves gemmi + the DB in-process then
  exits — for CI/smoke before shipping an installer.
- `backend.spec` — the PyInstaller recipe. Collects whole packages (Django
  resolves a lot by string at runtime) and the `gemmi` compiled extension.

## Build & run

```sh
pip install -r requirements.txt -r requirements-packaging.txt
pyinstaller packaging/backend.spec --noconfirm        # -> dist/pandda-inspect-backend

# smoke test (no server):
./dist/pandda-inspect-backend --selfcheck

# run the server (DB + data go in PANDDA_DB_DIR, NOT the bundle):
PANDDA_DB_DIR=~/pandda-data PANDDA_PORT=8000 ./dist/pandda-inspect-backend
# then GET http://127.0.0.1:8000/api/docs/
```

## Status (2026-06-01)

Spike PASSES: 23 MB onefile (arm64, Python 3.14, PyInstaller 6.20) boots,
migrates, serves `/api/{schema,docs,v1/projects}` (all 200), and gemmi (the
compiled wheel — the chief freeze risk) imports + is callable in-process.

## The Electron shell (built — see `../electron/` and `../package.json`)

The shell that spawns this binary now exists:

- `server_main.py` also **serves the built client** (`client/dist`) at `/` with
  COOP/COEP headers (`_SpaStaticApp`) — so the Electron window loads
  `http://127.0.0.1:PORT/` **same-origin** with `/api` and cross-origin-isolated
  for Moorhen's `SharedArrayBuffer`. No `file://`, no API-base rewrite.
- `backend.spec` bundles `client/dist` as `client_dist` in the freeze (only if
  the client was built first; CI always builds it before PyInstaller).
- `electron/main.js` picks a free port, spawns this binary with
  `PANDDA_DB_DIR`/`PANDDA_DATA_ROOT`/`PANDDA_JOBS_ROOT` pointed at the **data
  dir**, polls `/api/v1/projects/` until ready, then opens the window. SIGTERM
  (then SIGKILL) on quit.
- `electron/preload.js` exposes a minimal `window.panddaDesktop` bridge
  (contextBridge, sandboxed) — a native folder picker + the data-dir setting.
- `package.json` → `electron-builder` bundles this binary as an `extraResource`
  at `resources/backend/pandda-inspect-backend` and emits per-OS installers.
- `.github/workflows/electron.yml` runs the full pipeline on a mac/win/linux
  matrix: build client → freeze backend → `--selfcheck` → electron-builder.

### Ingest in place (no copy) — the desktop affordance

The spec calls out that "ingest without copy" is a CLI/Electron capability, not
a browser one (a browser file picker yields no path). The desktop shell unlocks
it: the **Browse folder…** button on the Import page calls
`window.panddaDesktop.pickDirectory()` (native dialog → a real path) and POSTs it
to `POST /api/v1/projects/ingest_path/`, which runs the existing
`ingest_pandda2`/`ingest_pandda --root` and sets `Project.source_root` to that
path **where it already lives** — nothing is copied. Artifact serving resolves
relpaths against `source_root`, so the in-place tree streams directly.

Security: that endpoint runs ingest against an arbitrary *server-side* path, so
it is **localhost-only** (`views._is_local_request` — loopback `REMOTE_ADDR`).
The desktop app and dev machine spawn the backend on `127.0.0.1`; a hosted
deployment refuses it (403) and uses the zip importer instead.

### Where data is written (configurable)

The backend writes the SQLite DB, refinement/job outputs, and zip-imported
(copied) data under the **data dir** — by default Electron's per-user `userData`
(always writable; the bundle is read-only). The user can relocate it in
**Settings → Data folder** (native picker, persisted to
`userData/config.json`); `main.js` reads it at spawn and sets the `PANDDA_*`
env, so a change prompts an in-app relaunch. Ingest-in-place projects are NOT
written here — they keep their own `source_root`.

### macOS: clickable app + Gatekeeper (unsigned)

The `dmg`/`zip` targets produce a double-clickable `Reinspect.app`. With no
Apple credentials the build is **unsigned**, so first launch hits Gatekeeper:
open it once via **right-click → Open** (or
`xattr -dr com.apple.quarantine "/Applications/Reinspect.app"`); subsequent
launches are normal.

### macOS: signing + notarization (the config is already wired)

Signing + notarization are **plumbed and self-gating** — they activate the
moment the right credentials are present and no-op otherwise (so forks/PRs still
build unsigned). The pieces, already in the repo:

- `electron/entitlements.mac.plist` — hardened-runtime entitlements. REQUIRED:
  the embedded PyInstaller backend unpacks + `dlopen`s code at runtime, which a
  default hardened runtime SIGKILLs; the `allow-unsigned-executable-memory` /
  `disable-library-validation` keys let the frozen interpreter + the compiled
  `gemmi` `.so` load. (Hardened runtime is itself a notarization prerequisite.)
- `electron/notarize.js` — the `afterSign` hook. Submits the *signed* app to
  Apple's notary service via `@electron/notarize`, but only when `APPLE_*` env
  is set; otherwise it logs a skip and returns.
- `package.json` `mac` block — `hardenedRuntime: true`, points at the
  entitlements, `notarize: false` (disables electron-builder's *built-in*
  notarize so it doesn't double-run our hook).
- `.github/workflows/electron.yml` passes `CSC_LINK` / `CSC_KEY_PASSWORD` and
  the `APPLE_*` secrets through to the build step.

**What a human still has to do (one-time):**

1. **Get a *Developer ID Application* certificate** (NOT the "Apple Development"
   cert Xcode auto-makes — that's local-only and can't ship). Easiest:
   Xcode → Settings → Accounts → select your Apple ID → *Manage Certificates* →
   `+` → **Developer ID Application**. Xcode generates the CSR, downloads, and
   installs it into your login keychain. Verify: `security find-identity -v -p
   codesigning` should now list a `Developer ID Application: …` identity.
   - If it fails, check for a pending **Program License Agreement** at
     developer.apple.com (everything silently fails until it's accepted).
   - NB the Developer ID team (`U72A26RZ2R`) may DIFFER from the team on the
     local Apple Development certs (`ZM2T59XTLY`) — use the Developer ID one.

   **Sign a build LOCALLY (proven working):** the keychain identity is used
   automatically when `CSC_LINK` is unset; force-select it by name (the name
   part only — electron-builder rejects the `Developer ID Application:` prefix):

   ```sh
   export CSC_IDENTITY_AUTO_DISCOVERY=true
   export CSC_NAME="Martin Noble (U72A26RZ2R)"
   npm run dist
   # verify: codesign --verify --deep --strict -v "release/mac-arm64/Reinspect.app"
   #   -> "valid on disk" + "satisfies its Designated Requirement"
   # spctl -a -t install <app> will say "rejected: Unnotarized Developer ID"
   #   until you do the notarization step below — that's expected, not a failure.
   ```
2. **Export the cert to a `.p12`** for CI: Keychain Access → find the
   "Developer ID Application" identity → right-click → Export → `.p12` with a
   password. Then base64 it: `base64 -i cert.p12 | pbcopy`.
3. **Make an app-specific password** at appleid.apple.com (Sign-In & Security →
   App-Specific Passwords) — this is `APPLE_APP_SPECIFIC_PASSWORD`, not your
   Apple ID password.
4. **Add the GitHub repo secrets** (Settings → Secrets and variables → Actions):
   - `CSC_LINK` = the base64 `.p12` from step 2
   - `CSC_KEY_PASSWORD` = that `.p12`'s password
   - `APPLE_ID` = your Apple ID email
   - `APPLE_APP_SPECIFIC_PASSWORD` = step 3
   - `APPLE_TEAM_ID` = `U72A26RZ2R` (the Developer ID team — NB this differs
     from the `ZM2T59XTLY` team on the local "Apple Development" certs)

After that, a tagged/main CI build signs + notarizes automatically. To sign a
build **locally** instead, export the same vars in your shell before
`npm run dist` (the installed keychain identity is used if `CSC_LINK` is unset).

**Windows** signing is the analogous story (`CSC_LINK`/`CSC_KEY_PASSWORD` with a
Windows code-signing cert); not set up yet, and unsigned `.exe`/`nsis` installers
work with a SmartScreen "More info → Run anyway".

Build the desktop app locally (from repo root):

```sh
cd client && npm ci && npm run build && cd ..        # 1. client/dist
pip install -r requirements.txt -r requirements-packaging.txt
pyinstaller packaging/backend.spec --noconfirm        # 2. dist/pandda-inspect-backend (now incl. client)
npm ci && npm run dist                                # 3. release/<installers>
# dev iteration on the shell (uses dist/ binary, skips installer):
npm start
```

## Gotchas (see also the electron-packaging-spike memory)

- `python-dotenv` is imported at the top of `config/settings.py` and is NOT
  auto-detected — it's an explicit `hiddenimport` in the spec. Anything imported
  at settings-module top before app load needs the same.
- A frozen binary launched in the background fails **silently** (crash before
  the first stderr write). Run it foreground with a timeout to see the traceback.
- Run from source by PATH (`python packaging/server_main.py`), not
  `python -m packaging.server_main` — `packaging` shadows the installed lib.
- If `npm start` crashes with `Cannot read properties of undefined (reading
  'requestSingleInstanceLock')` + a `Node.js vXX` banner, the environment has
  `ELECTRON_RUN_AS_NODE=1` set — it forces Electron to run as plain Node so
  `require('electron')` returns no `app`. Unset it before launching. Not a bug
  in `main.js`.
