"""Small, typed helpers to read configuration from environment variables."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def env_str(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Missing required environment variable {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean, got {raw!r}")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_secret_list(name: str) -> list[str]:
    """Secrets from the file named by `<name>_FILE` (one per line, `#` comments), else `name`.

    The file form keeps keys out of the process environment (e.g. a Podman secret, SEC.3).
    """
    path = os.environ.get(f"{name}_FILE", "")
    if not path:
        return env_list(name)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ImproperlyConfigured(f"{name}_FILE: cannot read {path} ({exc.strerror})") from None
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def ensure_key_file(path: Path, *, key_id: str) -> None:
    """Create `path` with one new field encryption key, readable by the owner only, unless it
    exists: replacing a key would make the data it encrypted unreadable (dev settings, SEC.3)."""
    if path.exists():
        return
    from apps.core.crypto import generate_key

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("# MPMV field encryption keys (SEC.3), newest first. Never commit.\n")
        handle.write(generate_key(key_id) + "\n")
