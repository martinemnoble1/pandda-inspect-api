"""
Minimal Django settings for the pandda-inspect-api reference backend.

Deliberately thin: SQLite, no auth, CORS open for local client experiments.
The point is the contract and data model, not deployment hardening.

Config that varies by deployment crosses in via **environment variables** — the
same mechanism every binding uses (Electron injects them when it spawns this
backend, docker-compose sets them under ``environment:``, dev exports them or
uses a ``.env``). See docs/SETUP.md and DESIGN-artifacts-and-jobs.md §5.7.
Only *paths* + the refinement activation recipe are env-driven so far;
SECRET_KEY/DEBUG/ALLOWED_HOSTS hardening is deferred to the binding steps
(ROADMAP #5/#6) where it is actually exercised.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env if present. ``override=False`` (the default) means real
# environment variables — those Electron/compose inject — WIN over the file, so
# the file is only a dev convenience, never authoritative in a packaged build.
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "dev-only-not-secret-reference-implementation"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "inspect_api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# App-dirs template loading so DRF / drf-spectacular can find their templates
# (the Swagger UI at /api/docs/ needs this).
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Writable path: in a packaged (read-only) app bundle this MUST point
        # at a user-writable location, so it is env-overridable. Defaults near
        # the repo for dev. (DESIGN §5.7.)
        "NAME": os.environ.get("PANDDA_DB_PATH") or (BASE_DIR / "db.sqlite3"),
    }
}

# Where ingested PanDDA project trees live, so the API can stream artifacts.
# Per-project ``source_root`` (set at ingest) is the primary resolver; this is
# the fallback root for projects landed by the zip importer. Env-overridable.
PANDDA_DATA_ROOT = Path(
    os.environ.get("PANDDA_DATA_ROOT") or (BASE_DIR / "data")
)

# Where job working dirs (refinement outputs etc.) are written. Defaults to the
# data root so refined Artifact relpaths resolve through the same logic; a
# packaged binding points it at a user-writable location. (DESIGN §5.2/§5.7.)
PANDDA_JOBS_ROOT = Path(
    os.environ.get("PANDDA_JOBS_ROOT") or PANDDA_DATA_ROOT
)

# --- Refinement activation recipe (DESIGN §5.6) ---------------------------
# giant.quick_refine is NOT a bare-PATH binary: it needs CCP4 set up THEN the
# PanDDA2 conda env activated (in that order, so the PanDDA2 tool wins over
# CCP4's PanDDA1 giant.refine). These point at the host's setup scripts; unset
# ⇒ refinement dispatch is gated. Host-specific, kept out of the API/JobSpec.
CCP4_SETUP_SH = os.environ.get("CCP4_SETUP_SH", "")
CONDA_SH = os.environ.get("CONDA_SH", "")
PANDDA2_CONDA_ENV = os.environ.get("PANDDA2_CONDA_ENV", "")
# Refinement engine invoked AFTER activation. servalcat (modern CCP4) by
# default, refmac5 as a fallback/override — NOT giant.quick_refine, which is a
# non-reproducible wrapper (DESIGN §5.8). Tests override with a stand-in.
REFINE_TOOL = os.environ.get("REFINE_TOOL", "servalcat")

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Base URL of the Reinspect UI, used to build the ``ui_url`` a run-trigger
# caller (Materia) redirects to. Empty ⇒ derive from the request origin
# (fine for the single-origin desktop/dev binding); set it in a split-origin
# cloud deploy where the UI host differs. See docs/RUN_LIFECYCLE.md.
REINSPECT_UI_BASE_URL = os.environ.get("REINSPECT_UI_BASE_URL", "")

CORS_ALLOW_ALL_ORIGINS = True

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": (
        "rest_framework.pagination.LimitOffsetPagination"
    ),
    "PAGE_SIZE": 100,
    # This thin reference deliberately omits django.contrib.auth from
    # INSTALLED_APPS, so disable DRF's auth/permission machinery (which would
    # otherwise pull in AnonymousUser -> auth models). Auth is a deployment
    # concern layered on later, not part of the contract demo.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "pandda.inspect API",
    "DESCRIPTION": (
        "Contract-first reference API for pandda.inspect. Datasets, events "
        "(with mutable inspection decisions), and artifact references derived "
        "from a PanDDA analysis ingested into a transactional store."
    ),
    "VERSION": "0.1.1",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- Optional cloud auth (opt-in, OFF by default) -------------------------
# PRINCIPAL DELIVERY MODE PROTECTION: the standalone Electron desktop app never
# sets ``PANDDA_AUTH_BACKEND`` (it injects only PANDDA_* env at spawn — see
# electron/main.js), so with this unset the block below is inert and
# INSTALLED_APPS / MIDDLEWARE / REST_FRAMEWORK above are exactly as shipped.
# Nothing here can challenge the desktop client, which sends no token.
#
# When ``PANDDA_AUTH_BACKEND=ccp4i2`` we layer in the shared CCP4i2 auth
# contract (the opt-in ``ccp4i2-api`` package; see requirements-cloud.txt):
# exactly ONE auth middleware is selected by deployment shape and a DRF auth
# class surfaces ``request.user``. We deliberately set NO global
# ``IsAuthenticated`` — enforcement, when wanted, comes from the active
# middleware itself (each returns its own 401). See docs/MATERIA_INTEGRATION.md
# R2/R3 and the ccp4i2_api middleware docstrings.
PANDDA_AUTH_BACKEND = os.environ.get("PANDDA_AUTH_BACKEND", "").lower()

if PANDDA_AUTH_BACKEND == "ccp4i2":
    # contrib.auth backs the middleware's get_user_model(); added ONLY on this
    # opt-in path so the desktop build's apps + migrations stay unchanged.
    if "django.contrib.auth" not in INSTALLED_APPS:
        INSTALLED_APPS.append("django.contrib.auth")

    # Select exactly one auth middleware by deployment shape, mirroring the
    # CCP4i2 settings module's own order (see ccp4i2_api.middleware.dev_admin).
    # Selecting one — not listing all three — avoids a later active middleware
    # clobbering the request.user an earlier one set.
    if os.environ.get("CCP4I2_REQUIRE_AUTH", "").lower() in (
        "true", "1", "yes",
    ):
        _auth_middleware = "ccp4i2_api.middleware.AzureADAuthMiddleware"
    elif os.environ.get("CCP4I2_LOCAL_SESSION_TOKEN"):
        _auth_middleware = "ccp4i2_api.middleware.LocalSessionAuthMiddleware"
    elif DEBUG:
        _auth_middleware = "ccp4i2_api.middleware.DevAdminMiddleware"
    else:
        # Production-shaped deploy with no auth env set: no middleware, so
        # requests stay unauthenticated (AllowAny) rather than auto-creating a
        # superuser. Strictly the safe fallback.
        _auth_middleware = None
    if _auth_middleware:
        MIDDLEWARE = MIDDLEWARE + [_auth_middleware]

    # Surface request.user to DRF. The auth class trusts ONLY users our
    # middleware set (via REQUEST_FLAG_ATTR), so it cannot be spoofed.
    # DEFAULT_PERMISSION_CLASSES stays empty (AllowAny) — no global enforcement.
    REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [
        "ccp4i2_api.drf.AzureADAuthentication"
    ]
