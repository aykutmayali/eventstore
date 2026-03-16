from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Use SQLite for local development without Docker
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Allow unauthenticated access in local dev for easier testing
REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = [  # type: ignore[name-defined]  # noqa: F405
    "rest_framework.permissions.AllowAny",
]
