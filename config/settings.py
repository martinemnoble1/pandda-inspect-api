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
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
