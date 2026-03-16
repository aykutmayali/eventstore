import os

from .base import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")

# Enforce a proper secret key in production
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
