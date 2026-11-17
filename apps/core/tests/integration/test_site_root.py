"""I18N.18: the default Site has a real hostname, and `site_root_url()` never returns an empty
string: it fails loudly where live data is held and logs an error elsewhere."""

import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django.test.utils import override_settings
from wagtail.models import Site

from apps.core.site_checks import check_site_hostname
from apps.core.site_url import absolute_url, site_root_url

pytestmark = pytest.mark.django_db


def default_site() -> Site:
    return Site.objects.get(is_default_site=True)


def set_site(**fields: object) -> None:
    Site.objects.filter(is_default_site=True).update(**fields)


def ids() -> list[str]:
    return [str(m.id) for m in check_site_hostname(None)]


@pytest.mark.parametrize("hostname", ["", "localhost", "https://shop.example", "shop.example/x"])
@override_settings(LIVE_DATA=True, DEBUG=False)
def test_a_bad_hostname_is_an_error_where_live_data_is_held(hostname: str) -> None:
    set_site(hostname=hostname)
    assert ids() == ["core.E921"]


@override_settings(LIVE_DATA=False, STAGE=True, DEBUG=False)
def test_the_placeholder_is_an_error_on_stage_too() -> None:
    set_site(hostname="localhost")
    assert ids() == ["core.E921"]


@override_settings(LIVE_DATA=False, STAGE=False, DEBUG=True)
def test_the_placeholder_is_only_a_warning_in_debug() -> None:
    set_site(hostname="localhost")
    assert ids() == ["core.W921"]


@override_settings(LIVE_DATA=True, DEBUG=False)
def test_a_real_hostname_passes() -> None:
    set_site(hostname="shop.example", port=443)
    assert ids() == []


@override_settings(LIVE_DATA=True, DEBUG=False)
def test_no_default_site_is_an_error() -> None:
    Site.objects.all().delete()
    assert ids() == ["core.E921"]


@override_settings(LIVE_DATA=True, DEBUG=False)
def test_site_root_url_raises_on_live_with_the_placeholder() -> None:
    set_site(hostname="localhost")
    with pytest.raises(ImproperlyConfigured):
        site_root_url(RequestFactory().get("/"))


@override_settings(LIVE_DATA=True, DEBUG=False)
def test_site_root_url_is_the_https_root_of_a_real_site() -> None:
    set_site(hostname="shop.example", port=443)
    assert site_root_url(RequestFactory().get("/")) == "https://shop.example"
    assert absolute_url(None, "/it/") == "https://shop.example/it/"


@override_settings(LIVE_DATA=False, STAGE=False)
def test_elsewhere_site_root_url_logs_an_error_and_is_never_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    Site.objects.all().delete()
    with caplog.at_level(logging.ERROR):
        root = site_root_url(RequestFactory().get("/"))
    assert root.startswith("http")
    assert "site" in caplog.text.lower()
