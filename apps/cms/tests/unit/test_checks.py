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
