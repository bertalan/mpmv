"""SEO.6 2/3: what purges the anonymous page cache (publish, unpublish, move, locale, settings,
restrictions), and the hooks of the vendors and events services.

Real pages are not stored until the switcher is plain links (slice 3), so these tests watch the
cache generation, which every stored key contains: a purge changes it.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.cache import caches
from wagtail.models import Locale, Page, PageViewRestriction, Site

from apps.cms.services import SeedReport, seed_site
from apps.cms.tests.factories import StandardPageFactory
from apps.seo import page_cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def commit_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test transaction never commits: run `on_commit` callbacks at once."""
    monkeypatch.setattr(
        "apps.seo.signals.transaction.on_commit", lambda func, **_kw: func(), raising=True
    )
    monkeypatch.setattr(
        "apps.core.public_cache.transaction.on_commit", lambda func, **_kw: func(), raising=True
    )


@pytest.fixture(autouse=True)
def empty_cache() -> None:
    caches["pagecache"].clear()


@pytest.fixture
def seeded(db: None) -> SeedReport:
    return seed_site()


def home() -> Page:
    return Site.objects.get(is_default_site=True).root_page


def generation() -> str:
    return page_cache._generation()


def test_publishing_a_change_purges(seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Before", slug="inval-seo6", parent=home())
    before = generation()
    page.title = "After"
    page.save_revision().publish()
    assert generation() != before


def test_unpublishing_purges(seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Gone", slug="gone-seo6", parent=home())
    before = generation()
    page.unpublish()
    assert generation() != before


def test_moving_a_page_purges(seeded: SeedReport) -> None:
    parent = StandardPageFactory.create(title="Parent", slug="parent-seo6", parent=home())
    page = StandardPageFactory.create(title="Mover", slug="mover-seo6", parent=home())
    before = generation()
    page.move(parent, pos="last-child")
    assert generation() != before


def test_deleting_a_live_page_purges(seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Deleted", slug="deleted-seo6", parent=home())
    before = generation()
    page.delete()
    assert generation() != before


def test_saving_a_draft_does_not_purge(seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Draft", slug="draft-seo6", parent=home())
    page.unpublish()
    before = generation()
    page.title = "Still a draft"
    page.save()
    assert generation() == before


def test_a_locale_change_purges(seeded: SeedReport) -> None:
    before = generation()
    locale = Locale.objects.first()
    assert locale is not None
    locale.save()
    assert generation() != before


def test_a_site_or_theme_setting_change_purges(seeded: SeedReport) -> None:
    site = Site.objects.get(is_default_site=True)
    before = generation()
    site.site_name = "Renamed"
    site.save()
    assert generation() != before
    before = generation()
    theme_settings = django_apps.get_model("themes", "ThemeSettings")  # no app imports themes
    theme_settings.for_site(site).save()
    assert generation() != before


def test_adding_or_lifting_a_view_restriction_purges(seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Soon private", slug="private-seo6", parent=home())
    before = generation()
    restriction = PageViewRestriction.objects.create(page=page, restriction_type="login")
    assert generation() != before
    before = generation()
    restriction.delete()
    assert generation() != before


def test_the_purge_runs_only_after_the_commit(
    django_capture_on_commit_callbacks: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    seeded: SeedReport,
) -> None:
    monkeypatch.undo()  # keep Django's own on_commit
    page = StandardPageFactory.create(title="Slow", slug="slow-seo6", parent=home())
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        before = generation()
        page.title = "Changed"
        page.save_revision().publish()
        assert generation() == before
    for callback in callbacks:
        callback()
    assert generation() != before


def test_a_vendor_closure_change_purges_the_public_pages() -> None:
    import datetime as dt

    from apps.core.audit import AuditContext
    from apps.vendors.services import add_closure, delete_closure
    from apps.vendors.tests.factories import VendorFactory

    ctx = AuditContext(actor_id=1, actor_label="vendor:owner", source="vendor_area", ip="127.0.0.1")
    vendor = VendorFactory.create()
    before = generation()
    add_closure(
        vendor, dt.date(2027, 8, 1), dt.date(2027, 8, 15), ctx=ctx, today=dt.date(2027, 6, 1)
    )
    assert generation() != before
    from apps.vendors.models import VendorClosure

    closure = VendorClosure.objects.get(vendor=vendor)
    before = generation()
    delete_closure(closure, ctx=ctx, today=dt.date(2027, 6, 1))
    assert generation() != before


def test_a_closure_dry_run_purges_nothing() -> None:
    import datetime as dt

    from apps.core.audit import AuditContext
    from apps.vendors.services import add_closure
    from apps.vendors.tests.factories import VendorFactory

    ctx = AuditContext(actor_id=1, actor_label="vendor:owner", source="vendor_area", ip="127.0.0.1")
    before = generation()
    add_closure(
        VendorFactory.create(),
        dt.date(2027, 8, 1),
        dt.date(2027, 8, 15),
        ctx=ctx,
        today=dt.date(2027, 6, 1),
        dry_run=True,
    )
    assert generation() == before


def test_a_vendor_profile_save_and_a_status_change_purge() -> None:
    from apps.core.audit import AuditContext
    from apps.vendors import engine
    from apps.vendors.services import change_status
    from apps.vendors.tests.factories import VendorFactory

    ctx = AuditContext(actor_id=1, actor_label="staff", source="manager", ip="127.0.0.1")
    vendor = VendorFactory.create(status=engine.ACTIVE)
    before = generation()
    change_status(vendor, engine.SUSPENDED, ctx=ctx)
    assert generation() != before


def test_the_seo_app_registers_the_page_cache_as_a_purge_listener() -> None:
    from apps.core import public_cache
    from apps.seo import signals

    assert signals.purge_on_request in public_cache._listeners


def test_an_editor_autosave_of_a_live_page_does_not_purge_but_publishing_does(
    seeded: SeedReport,
) -> None:
    page = StandardPageFactory.create(title="Live", slug="autosave-seo6", parent=home())
    before = generation()
    page.title = "Draft edit"
    page.save_revision()  # the editor's autosave: only revision bookkeeping is written
    assert generation() == before
    page.latest_revision.publish()
    assert generation() != before


def test_a_platform_operator_change_purges(seeded: SeedReport) -> None:
    from apps.core.tests.factories import PlatformOperatorFactory

    before = generation()
    PlatformOperatorFactory.create()  # rows are versions: a change is a new row
    assert generation() != before


def test_a_purge_makes_a_stored_page_a_miss_and_a_rollback_keeps_it(
    django_capture_on_commit_callbacks: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> None:
    monkeypatch.undo()  # keep Django's own on_commit, so a savepoint rollback discards callbacks
    from django.db import transaction
    from django.http import HttpRequest, HttpResponse
    from django.test import RequestFactory
    from django.urls import ResolverMatch

    from apps.core.public_cache import purge_public_pages
    from apps.seo.middleware import PageCacheMiddleware

    def request() -> HttpRequest:
        built = RequestFactory().get("/stub-seo6/", HTTP_HOST="testserver")
        built.LANGUAGE_CODE = "en"
        built.resolver_match = ResolverMatch(lambda r: None, (), {}, url_name="wagtail_serve")
        built._page_cache_unrestricted = True  # type: ignore[attr-defined]
        return built

    def view(_request: HttpRequest) -> HttpResponse:
        return HttpResponse("<html><body>x</body></html>", content_type="text/html")

    settings.PAGE_CACHE_ENABLED = True
    middleware = PageCacheMiddleware(view)
    middleware(request())
    assert middleware(request())["X-Page-Cache"] == "HIT"
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        try:
            with transaction.atomic():
                purge_public_pages("rolled back")
                raise RuntimeError
        except RuntimeError:
            pass
    assert callbacks == []
    assert middleware(request())["X-Page-Cache"] == "HIT"  # a rollback never purges
    with django_capture_on_commit_callbacks(execute=True):
        purge_public_pages("committed")
    assert middleware(request())["X-Page-Cache"] == "MISS"
