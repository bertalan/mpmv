"""Local development on a developer machine (database in an Apple `container`)."""

import os
from pathlib import Path

from .env import ensure_key_file

os.environ.setdefault("DJANGO_SECRET_KEY", "dev-only-insecure-key")
os.environ.setdefault("DATABASE_URL", "postgres://mpmv@127.0.0.1:55432/mpmv")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Field encryption key (SEC.3): generated once into the user's config directory, outside the
# repository and the database, so local data stays readable across restarts.
if not os.environ.get("MPMV_FIELD_ENCRYPTION_KEYS"):
    _keys = os.environ.get("MPMV_FIELD_ENCRYPTION_KEYS_FILE") or "~/.config/mpmv/dev-field-keys"
    _key_file = Path(_keys).expanduser()
    ensure_key_file(_key_file, key_id="dev")
    os.environ["MPMV_FIELD_ENCRYPTION_KEYS_FILE"] = str(_key_file)

from .base import *  # noqa: F403

DEBUG = True
ALLOW_SEED = True  # `make seed` (ARC.10)
