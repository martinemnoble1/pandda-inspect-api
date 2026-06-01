"""
Standalone backend entry point for the frozen (PyInstaller) Electron binding.

This is the laptop binding of the same contract architecture (ROADMAP #6):
the Django backend + SQLite + (later) the built client, served by a
pure-Python WSGI server (waitress — freezes cleanly, no fork/exec like
gunicorn). Electron's main process will spawn this binary and point a
BrowserWindow at it.

Run frozen: ``./server`` (PyInstaller onefile), or from source
``python -m packaging.server_main``. Env: PANDDA_PORT (default 8000),
PANDDA_DB_DIR (where the SQLite file + data live; default a per-user
app-data dir — NOT inside the read-only frozen bundle).
"""
import os
import sys
from pathlib import Path


def _bootstrap_paths() -> None:
    """Make the Django project importable from source OR frozen.

    Under PyInstaller, sys._MEIPASS is the unpacked bundle root; the project
    package (config/, inspect_api/) is collected there. From source, the repo
    root is the parent of this file's dir.
    """
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _writable_data_dir() -> Path:
    """A per-user writable dir for the SQLite DB + ingested data.

    The frozen bundle is read-only, so the DB must live outside it. Mirrors
    where an Electron app would put userData.
    """
    override = os.environ.get("PANDDA_DB_DIR")
    if override:
        d = Path(override)
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "pandda-inspect"
    elif sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home())
        d = Path(base) / "pandda-inspect"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(
            Path.home() / ".local" / "share"
        )
        d = Path(base) / "pandda-inspect"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    _bootstrap_paths()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Point the SQLite DB at a writable per-user dir before Django configures
    # (settings.py honours PANDDA_DB_PATH — the frozen bundle is read-only).
    data_dir = _writable_data_dir()
    os.environ.setdefault("PANDDA_DB_PATH", str(data_dir / "db.sqlite3"))

    import django

    django.setup()

    # --selfcheck: prove the risky bits work IN-PROCESS under freeze (gemmi
    # compiled extension callable, DB migratable), then exit 0. Used by the
    # packaging smoke test / CI before shipping an installer.
    if "--selfcheck" in sys.argv:
        import gemmi  # compiled extension — the main freeze risk

        from django.core.management import call_command

        call_command("migrate", interactive=False, verbosity=0)
        sys.stderr.write(
            f"[selfcheck] OK — gemmi {gemmi.__version__}, "
            f"django {django.get_version()}, DB migrated at {data_dir}\n"
        )
        return 0

    # Apply migrations so a fresh install has a schema (idempotent).
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=1)

    from config.wsgi import application

    port = int(os.environ.get("PANDDA_PORT", "8000"))
    from waitress import serve

    sys.stderr.write(
        f"[pandda-inspect] backend up on http://127.0.0.1:{port}  "
        f"(data: {data_dir})\n"
    )
    sys.stderr.flush()
    serve(application, host="127.0.0.1", port=port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
