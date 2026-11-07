"""Settings for the automated test suite."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")
os.environ.setdefault("DATABASE_URL", "postgres://mpmv@127.0.0.1:55432/mpmv")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver")

from .base import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast hashing in tests only

# Parallel runs (several agents or worktrees) each get their own test database.
DATABASES["default"]["TEST"] = {"NAME": os.environ.get("MPMV_TEST_DB_NAME", "test_mpmv")}  # noqa: F405
