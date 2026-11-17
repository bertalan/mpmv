"""I18N.18: absolute URLs come from MPMV_BASE_URL (never the Host header or the Site row); the
check fails the deploy where live data is held; the seed syncs the default Site to it."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django.test.utils import override_settings
from wagtail.models import Site

from apps.core.site_checks import check_site_hostname
from apps.core.site_root import sync_default_site
from apps.core.site_url import absolute_url, site_root_url

pytestmark = pytest.mark.django_db

REAL = "https://shop.example"


def default_site() -> Site:
    return Site.objects.get(is_default_site=True)


def set_site(**fields: object) -> None:
    Site.objects.filter(is_default_site=True).update(**fields)


def ids() -> list[str]:
    return [str(m.id) + m.__class__.__name__[0] for m in check_site_hostname(None)]


@pytest.mark.parametrize(
    "url", ["", "http://localhost:8000", "http://shop.example", "shop.example"]
)
@override_settings(LIVE_DATA=True, DEBUG=False)
def test_a_bad_base_url_is_an_error_where_live_data_is_held(url: str) -> None:
    with override_settings(WAGTAILADMIN_BASE_URL=url):
        assert "core.E921E" in ids()


@override_settings(
    LIVE_DATA=False, STAGE=True, DEBUG=False, WAGTAILADMIN_BASE_URL="http://localhost:8000"
)
def test_the_unset_base_url_is_an_error_on_stage_too() -> None:
    assert "core.E921E" in ids()


@override_settings(
    LIVE_DATA=False, STAGE=False, DEBUG=True, WAGTAILADMIN_BASE_URL="http://localhost:8000"
)
def test_the_unset_base_url_is_only_a_warning_in_debug() -> None:
    assert "core.E921E" not in ids()
    assert "core.W921W" in ids()


@override_settings(LIVE_DATA=True, DEBUG=False, WAGTAILADMIN_BASE_URL=REAL)
def test_a_real_https_base_url_passes_when_the_site_matches() -> None:
    set_site(hostname="shop.example", port=443)
    assert ids() == []


@override_settings(LIVE_DATA=True, DEBUG=False, WAGTAILADMIN_BASE_URL=REAL)
def test_a_site_row_that_disagrees_is_a_warning() -> None:
    set_site(hostname="localhost", port=80)
    assert ids() == ["core.W921W"]


@override_settings(LIVE_DATA=True, DEBUG=False, WAGTAILADMIN_BASE_URL="http://localhost:8000")
def test_site_root_url_raises_on_live_without_a_real_base_url() -> None:
    with pytest.raises(ImproperlyConfigured):
        site_root_url(RequestFactory().get("/"))


@override_settings(LIVE_DATA=True, DEBUG=False, WAGTAILADMIN_BASE_URL=REAL + "/")
def test_site_root_url_is_the_base_url_whatever_the_host_header_and_site_say() -> None:
    set_site(hostname="elsewhere.example", port=443)
    request = RequestFactory().get("/", HTTP_HOST="evil.example")
    assert site_root_url(request) == REAL
    assert absolute_url(None, "/it/") == REAL + "/it/"
    assert absolute_url(None, "https://x.example/a") == "https://x.example/a"


@override_settings(LIVE_DATA=False, STAGE=False, WAGTAILADMIN_BASE_URL="http://localhost:8000")
def test_elsewhere_the_base_url_is_used_as_it_is() -> None:
    assert site_root_url(None) == "http://localhost:8000"


def test_the_check_is_silent_before_the_site_table_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """`migrate` runs the database checks before it creates the tables (the upgrade path in CI)."""
    from django.db import connection

    monkeypatch.setattr(connection.introspection, "table_names", lambda *a, **k: [])
    with override_settings(WAGTAILADMIN_BASE_URL=REAL):
        assert check_site_hostname(None) == []


@override_settings(WAGTAILADMIN_BASE_URL="https://shop.example:8443")
def test_sync_sets_hostname_and_port_idempotently_and_dry_run_writes_nothing() -> None:
    set_site(hostname="localhost", port=80)
    assert len(sync_default_site(dry_run=True)) == 1
    assert default_site().hostname == "localhost"
    assert len(sync_default_site()) == 1
    assert (default_site().hostname, default_site().port) == ("shop.example", 8443)
    assert sync_default_site() == []


@override_settings(WAGTAILADMIN_BASE_URL="http://localhost:8000")
def test_sync_leaves_the_site_alone_without_a_real_base_url() -> None:
    set_site(hostname="kept.example", port=443)
    assert sync_default_site() == []
    assert default_site().hostname == "kept.example"


def test_the_first_bump_records_the_configured_languages(
    django_capture_on_commit_callbacks: object,
) -> None:
    from apps.core.models import SiteLanguages
    from apps.core.sitemap_version import bump_sitemap_version

    SiteLanguages.objects.all().delete()
    with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
        bump_sitemap_version()
    row = SiteLanguages.objects.get()
    assert row.codes
    assert row.sitemap_version == 1
