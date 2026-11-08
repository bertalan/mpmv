"""EVT.1: an `EventIndexPage` in every enabled locale (spec 15 "Languages", Q-72).

`migrate` adds the index under each existing home page; a home page published later (the seed,
or a new language added to settings) gets its index at once. The indexes of all languages are
translations of each other (one `translation_key`), at `/<lang>/events/`.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.sql import emit_post_migrate_signal
from django.test import override_settings
from wagtail.models import Locale, Page, Revision

from apps.cms.models import HomePage
from apps.cms.services import seed_site
from apps.cms.tests.factories import HomePageFactory
from apps.events.models import EventIndexPage
from apps.events.services import ensure_event_indexes
from apps.events.tests.factories import EventIndexPageFactory, EventPageFactory

remove_event_pages = importlib.import_module(
    "apps.events.migrations.0002_page_cleanup_and_status"
).remove_event_pages

pytestmark = pytest.mark.django_db

LANGUAGES_PLUS_ONE = [*settings.LANGUAGES, ("pt", "Português")]


def _codes() -> set[str]:
    return {code for code, _name in settings.LANGUAGES}


def test_migrate_gives_the_primary_home_its_events_index() -> None:
    home = HomePage.objects.get(locale=Locale.get_default())
    index = EventIndexPage.objects.child_of(home).get()
    assert index.slug == "events"
    assert index.live
    assert index.url_path.endswith("/events/")


def test_every_enabled_locale_gets_one_index_after_the_seed() -> None:
    seed_site()
    indexes = EventIndexPage.objects.all()
    assert {index.locale.language_code for index in indexes} == _codes()
    assert indexes.count() == len(_codes())
    assert len({index.translation_key for index in indexes}) == 1
    assert all(index.live and index.slug == "events" for index in indexes)
    italian = indexes.get(locale__language_code="it")
    assert italian.title == "Eventi"


def test_ensure_is_idempotent_and_reports_what_it_creates() -> None:
    seed_site()
    assert ensure_event_indexes().is_empty


def test_dry_run_creates_nothing() -> None:
    EventIndexPage.objects.all().delete()
    changes = ensure_event_indexes(dry_run=True)
    assert not changes.is_empty
    assert not EventIndexPage.objects.exists()


def test_post_migrate_restores_a_missing_index() -> None:
    EventIndexPage.objects.all().delete()
    emit_post_migrate_signal(verbosity=0, interactive=False, db="default")
    assert EventIndexPage.objects.filter(locale=Locale.get_default()).exists()


@override_settings(LANGUAGES=LANGUAGES_PLUS_ONE, WAGTAIL_CONTENT_LANGUAGES=LANGUAGES_PLUS_ONE)
def test_a_language_added_later_gets_its_index() -> None:
    seed_site()
    portuguese = EventIndexPage.objects.get(locale__language_code="pt")
    english = EventIndexPage.objects.get(locale__language_code="en")
    assert portuguese.translation_key == english.translation_key
    assert portuguese.title == "Events"  # no sample title: the primary language's


def test_a_home_page_publication_adds_its_index() -> None:
    home = HomePageFactory.create(live=False)
    assert not EventIndexPage.objects.child_of(home).exists()
    home.save_revision().publish()
    assert EventIndexPage.objects.child_of(home).live().count() == 1


def test_the_factory_makes_an_index_where_one_is_missing() -> None:
    index = EventIndexPageFactory.create()
    assert isinstance(index.get_parent().specific, HomePage)
    assert index.locale != Locale.get_default()


def test_a_home_with_another_events_page_is_skipped() -> None:
    """A page with the slug `events` that is not an index blocks nothing, not even migrate."""
    home = HomePageFactory.create()
    home.add_child(instance=Page(title="Our events", slug="events", locale=home.locale))
    changes = ensure_event_indexes()
    assert not EventIndexPage.objects.child_of(home).exists()
    assert all(change.key.split(":")[1] != home.locale.language_code for change in changes.changes)
    emit_post_migrate_signal(verbosity=0, interactive=False, db="default")


def test_the_reverse_migration_removes_the_event_pages() -> None:
    """`migrate events zero` leaves no page rows of a dropped content type behind."""
    seed_site()
    page = EventPageFactory.create()
    page.save_revision().publish()
    parent_ids = set(EventIndexPage.objects.values_list("pk", flat=True))
    homes = {index.get_parent().pk for index in EventIndexPage.objects.all()}

    remove_event_pages(django_apps, None)

    assert not Page.objects.filter(content_type__app_label="events").exists()
    assert not Page.objects.filter(pk__in=parent_ids | {page.pk}).exists()
    assert not Revision.objects.filter(
        base_content_type=ContentType.objects.get_for_model(Page), object_id=str(page.pk)
    ).exists()
    for home in Page.objects.filter(pk__in=homes):
        assert home.numchild == home.get_children().count()
    assert Page.find_problems() == ([], [], [], [], [])
