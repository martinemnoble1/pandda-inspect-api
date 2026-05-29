"""
Minimal Django settings for the pandda-inspect-api reference backend.

Deliberately thin: SQLite, no auth, CORS open for local client experiments.
The point is the contract and data model, not deployment hardening.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Where ingested PanDDA project trees live, so the API can stream artifacts.
# Set by ingest; this is the default sample location.
PANDDA_DATA_ROOT = Path(
    "/Users/nmemn/Developer/MoorhenPanddaApp/PanddaProjects"
)

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
