"""Django settings for the server-rendered EasyImports API client."""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url

# BASE_DIR is the ``web/`` directory that holds manage.py.
BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent.resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _external_step14_path(name: str) -> Path:
    raw = os.environ.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"Developer dry-run profile requires {name}.")
    candidate = Path(raw).expanduser().resolve()
    if candidate == REPOSITORY_ROOT or candidate.is_relative_to(REPOSITORY_ROOT):
        raise RuntimeError(f"{name} must resolve outside the repository.")
    return candidate


DEVELOPER_DRY_RUN_PROFILE = (
    os.environ.get("EASYIMPORTS_API_TARGET_PROFILE", "").strip()
    == "developer-dry-run-v1"
)
if DEVELOPER_DRY_RUN_PROFILE:
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "Developer dry-run profile requires an explicit external SQLite path."
        )
    STEP14_DB_PATH = _external_step14_path("EASYIMPORTS_DB_PATH")
    STEP14_MEDIA_ROOT = _external_step14_path("EASYIMPORTS_MEDIA_ROOT")


# SECURITY: override via the EASYIMPORTS_SECRET_KEY environment variable in
# any non-local deployment.
SECRET_KEY = os.environ.get(
    "EASYIMPORTS_SECRET_KEY",
    "django-insecure-dev-key-change-me-before-deploying-easyimports",
)

DEBUG = _env_bool("EASYIMPORTS_DEBUG", not bool(os.environ.get("RENDER")))

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("EASYIMPORTS_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("EASYIMPORTS_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    CSRF_TRUSTED_ORIGINS.append(f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "importer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "easyimports_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "easyimports_web.wsgi.application"
ASGI_APPLICATION = "easyimports_web.asgi.application"

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(
                STEP14_DB_PATH
                if DEVELOPER_DRY_RUN_PROFILE
                else os.environ.get("EASYIMPORTS_DB_PATH", BASE_DIR / "db.sqlite3")
            ),
            # Wait on write locks instead of failing immediately under concurrent
            # claim INSERT (CRM-dupe 4A unique attempt rows; SQLite default is 5s
            # or less depending on driver). Does not replace uniqueness — it only
            # reduces spurious "database is locked" errors during brief contention.
            "OPTIONS": {
                "timeout": 20,
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
if not DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

MEDIA_URL = "media/"
MEDIA_ROOT = (
    STEP14_MEDIA_ROOT
    if DEVELOPER_DRY_RUN_PROFILE
    else Path(os.environ.get("EASYIMPORTS_MEDIA_ROOT", BASE_DIR / "media"))
)

# Per-session working directories (uploads, generated tables, export bundle).
SESSIONS_ROOT = MEDIA_ROOT / "sessions"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HTTPS-2 freeze: OAuth top-level return requires SameSite=Lax (not Strict).
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# HTTPS loopback launcher (manage.py runserver_https / start_local_https.ps1).
# When set, force Secure cookies and trust https://127.0.0.1:8001 even in DEBUG.
_HTTPS_LOOPBACK = _env_bool("EASYIMPORTS_HTTPS_LOOPBACK", False)
if _HTTPS_LOOPBACK or not DEBUG:
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
else:
    CSRF_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False

if _HTTPS_LOOPBACK:
    _loopback_https_origin = "https://127.0.0.1:8001"
    if _loopback_https_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_loopback_https_origin)
    if ALLOWED_HOSTS != ["*"] and "127.0.0.1" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append("127.0.0.1")

# Salesforce and marketing-list exports can be large; allow generous request
# bodies. File uploads are streamed to temp files and are not counted against
# DATA_UPLOAD_MAX_MEMORY_SIZE, but we raise the limits to be safe.
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024 * 1024  # 256 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB (stream larger to disk)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000

# Default text encoding used when reading uploaded CSV files. Salesforce
# exports are commonly ISO-8859-1 (matches the pipeline default).
EASYIMPORTS_CSV_ENCODING = os.environ.get("EASYIMPORTS_CSV_ENCODING", "ISO-8859-1")

# The Django process is an HTTP-only EasyImports API consumer. It must never
# fall back to importing or running mappings_2 in-process.
EASYIMPORTS_API_BASE_URL = os.environ.get(
    "EASYIMPORTS_API_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")
EASYIMPORTS_API_VERSION = "1.2.0"
from easyimports_web.timeouts import parse_positive_timeout

EASYIMPORTS_API_CONNECT_TIMEOUT = parse_positive_timeout(
    "EASYIMPORTS_API_CONNECT_TIMEOUT",
    os.environ.get("EASYIMPORTS_API_CONNECT_TIMEOUT"),
    default="3",
)
EASYIMPORTS_API_READ_TIMEOUT = parse_positive_timeout(
    "EASYIMPORTS_API_READ_TIMEOUT",
    os.environ.get("EASYIMPORTS_API_READ_TIMEOUT"),
    default="30",
)
EASYIMPORTS_API_MUTATION_READ_TIMEOUT = parse_positive_timeout(
    "EASYIMPORTS_API_MUTATION_READ_TIMEOUT",
    os.environ.get("EASYIMPORTS_API_MUTATION_READ_TIMEOUT"),
    default="180",
)
EASYIMPORTS_API_LEASE_SAFETY_SECONDS = parse_positive_timeout(
    "EASYIMPORTS_API_LEASE_SAFETY_SECONDS",
    os.environ.get("EASYIMPORTS_API_LEASE_SAFETY_SECONDS"),
    default="15",
)

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# --- Billing / WooCommerce payments -----------------------------------------
# The "Pay with WooCommerce" option creates an order in a WooCommerce store via
# the WooCommerce REST API and redirects the customer to that order's payment
# page. All values are optional; when unset the pricing page explains that
# payments are not configured. Set these via environment variables / secrets.
#
#   WOOCOMMERCE_STORE_URL        e.g. https://shop.example.com
#   WOOCOMMERCE_CONSUMER_KEY     ck_xxx (WooCommerce > Settings > Advanced > REST API)
#   WOOCOMMERCE_CONSUMER_SECRET  cs_xxx
#   WOOCOMMERCE_PRODUCT_ID       numeric product id to purchase
#   WOOCOMMERCE_CHECKOUT_URL     optional static checkout/product URL fallback
EASYIMPORTS_PLAN_NAME = os.environ.get("EASYIMPORTS_PLAN_NAME", "EasyImports Pro")
EASYIMPORTS_DEBT_PRICE_FALLBACK_LABEL = os.environ.get(
    "EASYIMPORTS_DEBT_PRICE_FALLBACK_LABEL",
    "$39,345,340,787,969.72",
)
EASYIMPORTS_DEBT_PRICE_FALLBACK_DATE = os.environ.get(
    "EASYIMPORTS_DEBT_PRICE_FALLBACK_DATE",
    "2026-06-29",
)
EASYIMPORTS_DEBT_PRICE_TIMEOUT = float(
    os.environ.get("EASYIMPORTS_DEBT_PRICE_TIMEOUT", "5")
)
EASYIMPORTS_DEBT_PRICE_CACHE_SECONDS = int(
    os.environ.get("EASYIMPORTS_DEBT_PRICE_CACHE_SECONDS", "3600")
)
WOOCOMMERCE_STORE_URL = os.environ.get("WOOCOMMERCE_STORE_URL", "").strip()
WOOCOMMERCE_CONSUMER_KEY = os.environ.get("WOOCOMMERCE_CONSUMER_KEY", "").strip()
WOOCOMMERCE_CONSUMER_SECRET = os.environ.get("WOOCOMMERCE_CONSUMER_SECRET", "").strip()
WOOCOMMERCE_PRODUCT_ID = os.environ.get("WOOCOMMERCE_PRODUCT_ID", "").strip()
WOOCOMMERCE_CHECKOUT_URL = os.environ.get("WOOCOMMERCE_CHECKOUT_URL", "").strip()
WOOCOMMERCE_HTTP_TIMEOUT = int(os.environ.get("WOOCOMMERCE_HTTP_TIMEOUT", "20"))
