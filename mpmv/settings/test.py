"""Settings for the automated test suite."""

import os

from apps.core.crypto import generate_key

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")
os.environ.setdefault("DATABASE_URL", "postgres://mpmv@127.0.0.1:55432/mpmv")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver")
# A field encryption key generated for this process only (SEC.3): never written anywhere.
os.environ.setdefault("MPMV_FIELD_ENCRYPTION_KEYS", generate_key("test"))

from .base import *  # noqa: F403

# Feature flags: the code defaults, whatever the shell exports, read from the database on every
# check (tests roll back their writes, so a cached value could leak into the next test).
FEATURE_FLAGS = {}
FEATURE_FLAGS_CACHE_SECONDS = 0
# VIES (TAX.5): the suite never leaves the machine. A check without a fake fails the test, and the
# URL is the local discard port in case a test builds the real HTTP client from these settings.
VIES_CLIENT = "apps.tax.tests.fakes.RefusingViesClient"
VIES_API_URL = "http://127.0.0.1:9/"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast hashing in tests only

# Parallel runs (several agents or worktrees) each get their own test database.
DATABASES["default"]["TEST"] = {"NAME": os.environ.get("MPMV_TEST_DB_NAME", "test_mpmv")}  # noqa: F405

# EVT.12: no test reaches a geocoding provider. Adapters built without an explicit transport get
# one that refuses; tests use FakeGeocoder or a recording transport.
GEOCODING_TRANSPORT = "apps.core.geocoding.adapters.refuse_network"
GEOCODING_LOCATIONIQ_KEY = ""
GEOCODING_OPENCAGE_KEY = ""
