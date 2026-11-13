"""SEO.6: cached HTML never sits in a web-served folder, and production gives it its own volume."""

from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
QUADLETS = ROOT / "deploy" / "quadlet"


def _inside(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(Path(folder).resolve())


def test_the_page_cache_is_outside_media_and_static_roots() -> None:
    from mpmv.settings import base  # the real default; the test settings use a temp directory

    for location in (
        Path(base.CACHES["pagecache"]["LOCATION"]),
        Path(settings.CACHES["pagecache"]["LOCATION"]),
    ):
        assert not _inside(location, base.MEDIA_ROOT)
        assert not _inside(location, base.STATIC_ROOT)
        assert not _inside(location, settings.MEDIA_ROOT)
        assert not _inside(location, settings.STATIC_ROOT)


def test_no_system_check_is_silenced_for_the_cache() -> None:
    assert "caches.W002" not in getattr(settings, "SILENCED_SYSTEM_CHECKS", [])


def test_app_and_worker_mount_the_page_cache_volume_and_caddy_does_not() -> None:
    for name in ("mpmv-app.container", "mpmv-worker.container"):
        text = (QUADLETS / name).read_text()
        assert "Volume=mpmv-pagecache.volume:/app/var/page-cache:noexec,nosuid,nodev" in text
    assert "pagecache" not in (QUADLETS / "mpmv-caddy.container").read_text()
    unit = (QUADLETS / "mpmv-pagecache.volume").read_text()
    assert "VolumeName=mpmv-pagecache" in unit
    assert "User=10001" in unit
