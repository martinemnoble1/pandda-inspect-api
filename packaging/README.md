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

## Not yet built

- Electron shell (spawn this binary; `BrowserWindow` → the built client).
- Bundle + serve the built client from Django static (or load via Electron).
- Cross-platform CI (GH Actions matrix — each OS builds its own binary; the
  freeze is per-OS, so mac/win/linux artifacts are produced separately).

## Gotchas (see also the electron-packaging-spike memory)

- `python-dotenv` is imported at the top of `config/settings.py` and is NOT
  auto-detected — it's an explicit `hiddenimport` in the spec. Anything imported
  at settings-module top before app load needs the same.
- A frozen binary launched in the background fails **silently** (crash before
  the first stderr write). Run it foreground with a timeout to see the traceback.
- Run from source by PATH (`python packaging/server_main.py`), not
  `python -m packaging.server_main` — `packaging` shadows the installed lib.
