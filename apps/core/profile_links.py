"""Comparison form of an official profile link (pure; shared by the operator model and the
Organization JSON-LD)."""

from urllib.parse import urlsplit


def profile_key(url: str) -> str | None:
    """The comparison form of an https profile link (lower-case host in punycode, no trailing
    slash on the path); None for anything else, or a link with a user name or password."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or "@" in parts.netloc:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    port = f":{parts.port}" if parts.port not in (None, 443) else ""
    path = parts.path.rstrip("/")
    return f"https://{host}{port}{path}" + (f"?{parts.query}" if parts.query else "")
