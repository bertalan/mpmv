"""factory_boy factories for the events app (EVT.1, spec 15). Synthetic values only.

Event pages live in the Wagtail tree: `.create()` adds the page under `parent` with `add_child`.
`EventPageFactory` makes an unpublished (draft) trade-fair page of a new vendor, written in the
parent index's language, under the primary language's `EventIndexPage` unless `parent` is
given; publish it with `page.save_revision().publish()`. Traits: `promotion`, `online`,
`all_day`, `located` (a venue with coordinates) and `platform` (no vendor, staff only).

`EventIndexPageFactory` adds an index under the first home page without one, and creates a
home in a free language when every home has one. `.build()` stays off the database.
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest import mock

import factory
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.utils.text import slugify
from factory.django import DjangoModelFactory
from wagtail.models import Locale, Page, Site

from apps.cms.models import HomePage
from apps.cms.tests.factories import HomePageFactory
from apps.events.models import (
    EventIndexPage,
    EventPage,
    EventSettings,
    VendorEventPolicy,
)
from apps.vendors.tests.factories import VendorFactory

STARTS_AT = dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC)


def _unsaved_content_type(model: type[Model], for_concrete_model: bool = True) -> ContentType:
    return ContentType(app_label=model._meta.app_label, model=str(model._meta.model_name))


class _InTree:
    """Saves pages with treebeard's `add_child` under the `parent` declaration."""

    @classmethod
    def _build(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        kwargs.pop("parent", None)
        # A new Page looks up its ContentType on init; `.build()` stays off the database.
        with mock.patch.object(ContentType.objects, "get_for_model", _unsaved_content_type):
            return model_class(*args, **kwargs)

    @classmethod
    def _create(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        parent = kwargs.pop("parent", None) or cls._default_parent()
        kwargs.setdefault("locale", parent.locale)
        page = model_class(*args, **kwargs)
        parent.add_child(instance=page)
        return page

    @classmethod
    def _default_parent(cls) -> Page:
        raise NotImplementedError


def _home_without_index() -> HomePage:
    for home in HomePage.objects.order_by("path"):
        if not EventIndexPage.objects.child_of(home).exists():
            return home
    created: HomePage = HomePageFactory.create()
    return created


def primary_index() -> EventIndexPage:
    """The primary language's events index, created when missing."""
    index = EventIndexPage.objects.filter(locale=Locale.get_default()).first()
    if index is not None:
        return index
    home = HomePage.objects.filter(locale=Locale.get_default()).first()
    created: EventIndexPage = EventIndexPageFactory.create(parent=home)
    return created


class EventIndexPageFactory(_InTree, DjangoModelFactory[EventIndexPage]):
    class Meta:
        model = EventIndexPage

    title = "Events"
    slug = "events"
    parent = None
    intro = "<p>Fairs, promotions and meetings.</p>"

    @classmethod
    def _default_parent(cls) -> Page:
        return _home_without_index()


class EventPageFactory(_InTree, DjangoModelFactory[EventPage]):
    class Meta:
        model = EventPage
        skip_postgeneration_save = True

    class Params:
        promotion = factory.Trait(
            event_type=EventPage.EventType.PROMOTION,
            attendance="",
            venue_name="",
            city="",
            country="",
        )
        online = factory.Trait(
            attendance=EventPage.Attendance.ONLINE,
            online_url="https://meet.example.com/fair",
            venue_name="",
            city="",
            country="",
        )
        all_day = factory.Trait(
            all_day=True,
            starts_at=None,
            ends_at=None,
            start_date=dt.date(2027, 8, 28),
            end_date=dt.date(2027, 9, 2),
        )
        located = factory.Trait(
            street="Via Esempio 1",
            postcode="20121",
            city="Milano",
            region="MI",
            country="IT",
            latitude=Decimal("45.464204"),
            longitude=Decimal("9.189982"),
            geo_source=EventPage.GeoSource.MAP,
        )
        platform = factory.Trait(vendor=None)

    title = factory.Sequence(lambda n: f"Trade fair {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    live = False
    vendor = factory.SubFactory(VendorFactory)
    event_type = EventPage.EventType.FAIR
    summary = "Meet the makers at the spring trade fair."
    all_day = False
    starts_at = STARTS_AT
    ends_at = factory.LazyAttribute(
        lambda page: page.starts_at + dt.timedelta(hours=8) if page.starts_at else None
    )
    start_date = None
    end_date = None
    timezone = "Europe/Rome"
    attendance = EventPage.Attendance.IN_PERSON
    venue_name = "Exhibition centre"
    city = "Milano"
    country = "IT"
    booth = factory.Sequence(lambda n: f"B{n}")
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Come and see us.</p>")])

    @classmethod
    def _default_parent(cls) -> Page:
        return primary_index()

    @classmethod
    def _create(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        parent = kwargs.get("parent") or cls._default_parent()
        kwargs["parent"] = parent
        kwargs.setdefault("original_locale", kwargs.get("locale") or parent.locale)
        return super()._create(model_class, *args, **kwargs)


class VendorEventPolicyFactory(DjangoModelFactory[VendorEventPolicy]):
    class Meta:
        model = VendorEventPolicy

    vendor = factory.SubFactory(VendorFactory)
    mode = VendorEventPolicy.Mode.ALWAYS_REVIEW


class EventSettingsFactory(DjangoModelFactory[EventSettings]):
    """The events settings of a new Wagtail site that serves the default site's page tree."""

    class Meta:
        model = EventSettings

    site = None
    moderation_threshold = 3
    cancelled_retention_days = 30

    @classmethod
    def _create(cls, model_class: type[EventSettings], *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("site") is None:
            default = Site.objects.filter(is_default_site=True).first()
            root = default.root_page if default is not None else Page.get_first_root_node()
            number = Site.objects.count()
            kwargs["site"] = Site.objects.create(
                hostname=f"events{number}.example.test", port=80, root_page=root
            )
        return model_class._default_manager.create(*args, **kwargs)
