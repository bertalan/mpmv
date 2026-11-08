"""EVT.1: page types, translation config and tenancy declarations of the events app.

Spec 15 "Page models" and "Languages" (owner decision Q-72): structured fields are identical in
every translation of an event (synchronised, not overridable); title, summary, body and venue
name are translatable.
"""

from django.db import models
from wagtail_localize.fields import SynchronizedField, TranslatableField, get_translatable_fields

from apps.cms.models import HomePage
from apps.events.models import EventIndexPage, EventPage, EventSettings, VendorEventPolicy
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedQuerySet, is_vendor_scoped, scope_target

SYNCHRONISED = (
    "vendor",
    "event_type",
    "original_locale",
    "all_day",
    "starts_at",
    "ends_at",
    "start_date",
    "end_date",
    "timezone",
    "rrule",
    "exdates",
    "exdate_days",
    "attendance",
    "online_url",
    "street",
    "postcode",
    "city",
    "region",
    "country",
    "booth",
    "latitude",
    "longitude",
    "geo_source",
    "geo_provider",
    "geo_precision",
    "geo_confidence",
    "geo_at",
    "listing_image",
    "sequence",
    "cancelled_at",
    "hidden_at",
    "hidden_by",
    "hidden_reason",
    "dsa_notice_ref",
    "content_changed_at",
)
TRANSLATABLE = ("title", "slug", "summary", "body", "venue_name")


def _fields(model: type) -> dict[str, SynchronizedField | TranslatableField]:
    return {field.field_name: field for field in get_translatable_fields(model)}


def test_the_home_page_allows_the_events_index_by_name() -> None:
    assert "events.EventIndexPage" in HomePage.subpage_types


def test_page_type_tree() -> None:
    assert EventIndexPage.parent_page_types == ["cms.HomePage"]
    assert EventIndexPage.subpage_types == ["events.EventPage"]
    assert EventIndexPage.max_count_per_parent == 1
    assert EventPage.parent_page_types == ["events.EventIndexPage"]
    assert EventPage.subpage_types == []


def test_structured_fields_are_synchronised_and_not_overridable() -> None:
    fields = _fields(EventPage)
    for name in SYNCHRONISED:
        field = fields[name]
        assert isinstance(field, SynchronizedField), name
        assert not field.overridable, name


def test_status_is_each_pages_own_and_never_copied_by_wagtail_localize() -> None:
    """A draft translation of a live event is a draft: `status` follows its own page's state."""
    assert "status" not in _fields(EventPage)
    assert not EventPage._meta.get_field("status").editable


def test_texts_are_translatable() -> None:
    fields = _fields(EventPage)
    for name in TRANSLATABLE:
        assert isinstance(fields[name], TranslatableField), name


def test_links_to_products_and_categories_are_parental_many_to_many() -> None:
    from modelcluster.fields import ParentalManyToManyField

    for name, target in (("products", "catalog.Product"), ("categories", "catalog.Category")):
        field = EventPage._meta.get_field(name)
        assert isinstance(field, ParentalManyToManyField)
        assert field.related_model._meta.label == target


def test_coordinates_have_six_decimal_places() -> None:
    for name in ("latitude", "longitude"):
        field = EventPage._meta.get_field(name)
        assert isinstance(field, models.DecimalField)
        assert (field.max_digits, field.decimal_places) == (9, 6)
        assert field.null


def test_event_pages_and_policies_are_vendor_scoped() -> None:
    for model in (EventPage, VendorEventPolicy):
        assert is_vendor_scoped(model), model
        assert scope_target(model) is Vendor
    # The page manager keeps Wagtail's page queryset (live(), child_of() ...) and adds scoping.
    assert isinstance(EventPage.objects.all(), VendorScopedQuerySet)
    assert hasattr(EventPage.objects.all(), "live")


def test_event_settings_defaults() -> None:
    settings = EventSettings()
    assert settings.moderation_threshold == 3
    assert settings.cancelled_retention_days == 30


def test_a_new_event_page_is_a_draft_in_the_platform_zone() -> None:
    page = EventPageFactory.build()
    assert page.status == EventPage.Status.DRAFT
    assert page.sequence == 0
    assert EventPage._meta.get_field("timezone").get_default() == "Europe/Rome"
