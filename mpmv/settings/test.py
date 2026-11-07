"""Settings for the automated test suite."""

import os

from apps.core.crypto import generate_key

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")
os.environ.setdefault("DATABASE_URL", "postgres://mpmv@127.0.0.1:55432/mpmv")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver")
# A field encryption key generated for this process only (SEC.3): never written anywhere.
os.environ.setdefault("MPMV_FIELD_ENCRYPTION_KEYS", generate_key("test"))

from .base import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast hashing in tests only

# Parallel runs (several agents or worktrees) each get their own test database.
DATABASES["default"]["TEST"] = {"NAME": os.environ.get("MPMV_TEST_DB_NAME", "test_mpmv")}  # noqa: F405
