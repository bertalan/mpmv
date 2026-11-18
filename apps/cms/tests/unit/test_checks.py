"""CMS.1: `manage.py check --deploy` requires the public base URL of the site (cms.E001).

Wagtail puts WAGTAILADMIN_BASE_URL in the links of its admin emails and previews; without
MPMV_BASE_URL the settings fall back to localhost, which is right on a laptop only.
"""

import pytest
from django.core import checks
from django.test import override_settings

from apps.cms.checks import check_base_url


@pytest.mark.parametrize(
    "url",
    [
        "",
        None,
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "not a url",
    ],
)
def test_a_missing_or_local_base_url_fails_the_deploy_check(url: str | None) -> None:
    with override_settings(WAGTAILADMIN_BASE_URL=url):
        errors = check_base_url(None)
    assert [error.id for error in errors] == ["cms.E001"]
    assert "MPMV_BASE_URL" in (errors[0].hint or "")


def test_a_public_base_url_passes() -> None:
    with override_settings(WAGTAILADMIN_BASE_URL="https://shop.example.com"):
        assert check_base_url(None) == []


def _registered(*, deploy: bool) -> list[object]:
    return list(checks.registry.registry.get_checks(include_deployment_checks=deploy))


def test_the_base_url_is_checked_on_deploy_only() -> None:
    assert check_base_url in _registered(deploy=True)
    assert check_base_url not in _registered(deploy=False)


# --- cms.E003: MPMV_VIDEO_EMBED_ORIGINS (THM.14) ---------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "http://www.youtube-nocookie.com",
        "*",
        "https://*",
        "https://*.example.com",
        "https://player.example.com/embed",
        "https://player.example.com/",
        "https://player.example.com:8443",
        "https://player.example.com?x=1",
        "player.example.com",
        "",
    ],
)
def test_a_video_origin_that_is_not_a_plain_https_origin_fails_the_check(origin: str) -> None:
    from apps.cms.checks import check_video_embed_origins

    with override_settings(MPMV_VIDEO_EMBED_ORIGINS=["https://player.vimeo.com", origin]):
        errors = check_video_embed_origins(None)
    assert [error.id for error in errors] == ["cms.E003"]
    assert "MPMV_VIDEO_EMBED_ORIGINS" in errors[0].msg


@pytest.mark.parametrize(
    "origins",
    [
        ("https://www.youtube-nocookie.com", "https://player.vimeo.com"),
        ["https://video.example.org"],
        [],
    ],
)
def test_plain_https_video_origins_pass_the_check(origins: object) -> None:
    from apps.cms.checks import check_video_embed_origins

    with override_settings(MPMV_VIDEO_EMBED_ORIGINS=origins):
        assert check_video_embed_origins(None) == []


def test_the_video_origins_check_is_registered_and_the_default_is_unchanged() -> None:
    from django.conf import settings

    from apps.cms.checks import check_video_embed_origins

    assert check_video_embed_origins in _registered(deploy=False)
    assert list(settings.MPMV_VIDEO_EMBED_ORIGINS) == [
        "https://www.youtube-nocookie.com",
        "https://player.vimeo.com",
    ]
