"""Settings for the automated test suite."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")
os.environ.setdefault("DATABASE_URL", "postgres://mpmv@127.0.0.1:55432/mpmv")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver")

from .base import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast hashing in tests only
