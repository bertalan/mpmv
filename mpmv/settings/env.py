"""Small, typed helpers to read configuration from environment variables."""

import os
from pathlib import Path
from urllib.parse import urlsplit

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


def env_flags(name: str) -> dict[str, bool]:
    """Feature flag defaults (ARC.8), e.g. `checkout=off,beta_search=on`; names checked later
    against the registry by the core.E401 system check."""
    flags: dict[str, bool] = {}
    for item in env_list(name):
        key, _, raw = item.partition("=")
        key, value = key.strip(), raw.strip().lower()
        if not key or value not in (_TRUE | _FALSE) - {""}:
            raise ImproperlyConfigured(f"{name}: expected name=on or name=off, got {item!r}")
        flags[key] = value in _TRUE
    return flags


def env_secret_list(name: str) -> list[str]:
    """Secrets from the file named by `<name>_FILE` (one per line, `#` comments), else `name`.

    The file form keeps keys out of the process environment (e.g. a Podman secret, SEC.3).
    Both set (not empty) is a configuration error: silently preferring one of them would hide
    a key someone meant to use.
    """
    path = os.environ.get(f"{name}_FILE", "")
    if not path:
        return env_list(name)
    if env_list(name):
        raise ImproperlyConfigured(f"{name} and {name}_FILE are both set: keep one of them.")
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


def map_tile_origin(url: str) -> str | None:
    """The origin the CSP must allow for the map tile URL template (EVT.13); None for the site.

    `MPMV_MAP_TILE_URL` is a Leaflet template with `{z}`, `{x}` and `{y}`: a path on the site
    itself (the EVT.14 tile proxy, `/maps/tiles/{z}/{x}/{y}.png`) needs nothing; an absolute URL
    must be HTTPS on one fixed host, and the CSP then allows that origin only (no `{s}`
    subdomains, no scheme-relative URL). Empty: maps show no tile layer.
    """
    if not url:
        return None
    problem = ""
    if not all(part in url for part in ("{z}", "{x}", "{y}")):
        problem = "needs the {z}, {x} and {y} placeholders"
    elif url.startswith("/") and not url.startswith("//"):
        return None
    else:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            problem = "must be a path on this site or an https:// URL"
        elif "{" in parts.netloc or "}" in parts.netloc:
            problem = "must name one fixed host"
    if problem:
        raise ImproperlyConfigured(f"MPMV_MAP_TILE_URL {problem}, got {url!r}")
    return f"https://{urlsplit(url).netloc}"
