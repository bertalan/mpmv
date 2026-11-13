"""SEO.6: the page cache lives inside MEDIA_ROOT, so Caddy must never serve it (caches.W002)."""

import re
from pathlib import Path

from django.conf import settings

CADDYFILE = Path(settings.BASE_DIR) / "deploy" / "caddy" / "Caddyfile"


def test_caddy_serves_only_the_image_folders_under_media() -> None:
    text = CADDYFILE.read_text()
    match = re.search(r"path_regexp public_media (\S+)", text)
    assert match
    public = re.compile(match.group(1))
    assert public.match("/media/images/abc.webp")
    assert not public.match("/media/.page-cache/1a2b3c.djcache")
    assert re.search(r"handle /media/\*\s*\{\s*respond 404", text)


def test_the_silenced_check_is_only_the_media_root_cache_warning() -> None:
    assert settings.SILENCED_SYSTEM_CHECKS == ["caches.W002"]
