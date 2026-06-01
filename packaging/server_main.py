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
import mimetypes
import os
import sys
from pathlib import Path

# Cross-origin isolation headers. Moorhen's WASM needs SharedArrayBuffer, which
# the browser only grants to a cross-origin-isolated context. Served same-origin
# with the API, these headers make the whole app (client + /api) isolated.
_COOP_COEP = [
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Embedder-Policy", "require-corp"),
    # COEP:require-corp means same-origin subresources need this too; setting it
    # broadly keeps WASM/worker/asset loads from being blocked.
    ("Cross-Origin-Resource-Policy", "same-origin"),
]


def _client_dist() -> "Path | None":
    """Locate the built client (``client/dist``), frozen or from source.

    Frozen: collected at ``<_MEIPASS>/client_dist`` (see backend.spec). From
    source: ``<repo>/client/dist`` if it has been built. Returns None if the
    client was never built (backend still serves /api; the window just 404s).
    """
    if getattr(sys, "frozen", False):
        cand = Path(sys._MEIPASS) / "client_dist"  # type: ignore[attr-defined]
    else:
        cand = Path(__file__).resolve().parent.parent / "client" / "dist"
    return cand if (cand / "index.html").is_file() else None


class _SpaStaticApp:
    """Serve the built SPA at ``/`` and delegate ``/api`` to Django.

    A deliberately tiny WSGI shim (no whitenoise dependency — keeps the freeze
    lean and the gotcha surface small). Adds COOP/COEP to every response so the
    client is cross-origin-isolated. Unknown non-/api paths fall back to
    ``index.html`` (client-side routing via react-router).
    """

    def __init__(self, django_app, dist: Path) -> None:
        self._django = django_app
        self._dist = dist.resolve()

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path.startswith("/api/") or path == "/api":
            return self._wrap_django(environ, start_response)
        return self._serve_static(path, start_response)

    def _wrap_django(self, environ, start_response):
        def _start(status, headers, exc_info=None):
            return start_response(status, headers + _COOP_COEP, exc_info)

        return self._django(environ, _start)

    def _serve_static(self, path, start_response):
        rel = path.lstrip("/") or "index.html"
        target = (self._dist / rel).resolve()
        # Path traversal guard + SPA fallback for unknown routes.
        if (
            self._dist not in target.parents and target != self._dist
        ) or not target.is_file():
            target = self._dist / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        headers = [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
        ] + _COOP_COEP
        start_response("200 OK", headers)
        return [body]


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

    # Serve the built client same-origin with the API (so its relative
    # /api/v1 calls work and the whole context is cross-origin-isolated for
    # Moorhen's SharedArrayBuffer). If the client was never built, fall back
    # to the bare Django app (the Electron window will just 404 at /).
    mimetypes.add_type("application/wasm", ".wasm")
    dist = _client_dist()
    if dist is not None:
        app = _SpaStaticApp(application, dist)
        client_note = f"client: {dist}"
    else:
        app = application
        client_note = "client: NOT BUILT (serving /api only)"

    port = int(os.environ.get("PANDDA_PORT", "8000"))
    from waitress import serve

    sys.stderr.write(
        f"[pandda-inspect] backend up on http://127.0.0.1:{port}  "
        f"(data: {data_dir}; {client_note})\n"
    )
    sys.stderr.flush()
    serve(app, host="127.0.0.1", port=port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
