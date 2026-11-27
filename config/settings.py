"""Django settings for warehouse-api. All deploy-specific values come from env vars (SPEC 0.1)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_files():
    """Local dev convenience: read KEY=VALUE lines from warehouse-api/.env and ../local-dev.env.
    Variables already set in the real environment always win, so hosted deploys are unaffected."""
    for path in (BASE_DIR / ".env", BASE_DIR.parent / "local-dev.env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key:
                os.environ.setdefault(key, val)


_load_env_files()


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip().rstrip("/")]


DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
# Render sets RENDER_EXTERNAL_HOSTNAME to the service's own *.onrender.com name; always accept it.
RENDER_HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if RENDER_HOST and RENDER_HOST not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_HOST)
# Local mode = only served on this machine. A hosted deploy sets DJANGO_ALLOWED_HOSTS to its domain.
LOCAL_MODE = all(h in ("127.0.0.1", "localhost") for h in ALLOWED_HOSTS)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG or LOCAL_MODE:
        SECRET_KEY = "django-insecure-dev-only-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when hosting (DJANGO_ALLOWED_HOSTS is not local) and DJANGO_DEBUG is off.")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "accounts",
    "warehouse",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", "").strip() or str(BASE_DIR / "db.sqlite3"),
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# Warehouse local time: "today" and the 7-day chart use IST day boundaries.
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["accounts.auth.ExpiringTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "warehouse.exceptions.api_exception_handler",
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
CORS_ALLOWED_ORIGIN_REGEXES = (
    [r"^https?://localhost(:\d+)?$", r"^https?://127\.0\.0\.1(:\d+)?$"] if (DEBUG or LOCAL_MODE) else []
)
CORS_ALLOW_HEADERS = ["accept", "authorization", "content-type", "idempotency-key", "origin", "x-requested-with"]
CORS_EXPOSE_HEADERS = ["idempotent-replayed"]

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

# Email (low-battery alerts). Credentials come from env only; never hard-code them.
MAILER_MAIL = os.environ.get("MAILER_MAIL", "").strip()
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend" if MAILER_MAIL else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("MAILER_ADDRESS", "smtp.gmail.com").strip()
EMAIL_PORT = int(os.environ.get("MAILER_PORT", "587"))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = MAILER_MAIL
EMAIL_HOST_PASSWORD = os.environ.get("MAILER_PASS", "").replace(" ", "")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = f'{os.environ.get("MAILER_FROM_NAME", "Alert").strip()} <{MAILER_MAIL or "noreply@localhost"}>'
# Comma-separated recipients; defaults to the sender mailbox.
LOW_BATTERY_ALERT_TO = env_list("LOW_BATTERY_ALERT_TO", MAILER_MAIL)

PASSWORD_RESET_URL = os.environ.get("PASSWORD_RESET_URL", "http://127.0.0.1:5173/reset-password").strip()
PASSWORD_RESET_TIMEOUT = 60 * 60

# Simulated shuttle battery (warehouse/simulation.py). Set SHUTTLE_SIMULATION=0 to turn it off.
SHUTTLE_SIMULATION = env_bool("SHUTTLE_SIMULATION", True)
