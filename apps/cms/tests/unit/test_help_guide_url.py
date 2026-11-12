"""ARC.32 security review: the vendor guide address is an absolute https URL or empty (cms.E002)."""

import pytest
from django.core import checks
from django.test import override_settings

from apps.cms import engine
from apps.cms.checks import check_vendor_guide_url

VALID = ["https://guide.example.org/vendors/", "https://guide.example.org"]
INVALID = [
    "http://guide.example.org/",
    "/guide/",
    "//guide.example.org/",
    "javascript:alert(1)",
    "data:text/html,x",
    "https://",
    "https:///path",
    "https://exa mple.org/",
    "https://guide.example.org/\n",
    "guide.example.org",
]


@pytest.mark.parametrize("url", VALID)
def test_an_absolute_https_url_is_valid(url: str) -> None:
    assert engine.help_guide_url(url) == url


@pytest.mark.parametrize("url", ["", None, "   "])
def test_empty_means_no_link(url: str | None) -> None:
    assert engine.help_guide_url(url) == ""
    assert engine.help_guide_url_valid(url)


@pytest.mark.parametrize("url", INVALID)
def test_anything_else_is_no_link_and_not_valid(url: str) -> None:
    assert engine.help_guide_url(url) == ""
    assert not engine.help_guide_url_valid(url)


@pytest.mark.parametrize("url", [*VALID, ""])
def test_the_check_passes_for_a_valid_or_empty_setting(url: str) -> None:
    with override_settings(MPMV_VENDOR_GUIDE_URL=url):
        assert check_vendor_guide_url(None) == []


@pytest.mark.parametrize("url", INVALID)
def test_the_check_fails_for_anything_else(url: str) -> None:
    with override_settings(MPMV_VENDOR_GUIDE_URL=url):
        errors = check_vendor_guide_url(None)
    assert [error.id for error in errors] == ["cms.E002"]
    assert "https" in (errors[0].hint or "")


def test_the_check_runs_in_every_check_run() -> None:
    registered = checks.registry.registry.get_checks(include_deployment_checks=False)
    assert check_vendor_guide_url in list(registered)  # type: ignore[comparison-overlap]
