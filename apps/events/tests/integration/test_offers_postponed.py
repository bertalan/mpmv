"""EVT.10 follow-up 1/2: ticket link, price, free entry and the postponed state (spec 15).

Everything is optional: an empty value means "not stated" and nothing is shown for it.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.events import services
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index

pytestmark = pytest.mark.django_db

TABLE = "events_eventpage"
PREVIOUS = "0005_field_help"
NEW_FIELDS = ("ticket_url", "price", "is_free", "postponed", "previous_start")


def test_a_new_event_states_no_offer_and_is_not_postponed() -> None:
    page = EventPageFactory.create(located=True)
    assert page.ticket_url == ""
    assert page.price is None
    assert page.is_free is False
    assert page.postponed is False
    assert page.previous_start is None
    page.full_clean()


def test_the_offer_and_postponed_fields_save_with_decimal_money() -> None:
    page = EventPageFactory.create(located=True)
    page.ticket_url = "https://tickets.example.com/fair"
    page.price = Decimal("12.50")
    page.postponed = True
    page.previous_start = page.starts_at
    page.full_clean()
    page.save()
    stored = EventPage.objects.get(pk=page.pk)
    assert stored.price == Decimal("12.50")
    assert stored.ticket_url == "https://tickets.example.com/fair"
    assert stored.postponed is True
    assert stored.previous_start == page.starts_at


def _refused(page: EventPage, field: str, **values: Any) -> None:
    for name, value in values.items():
        setattr(page, name, value)
    with pytest.raises(ValidationError) as error:
        page.full_clean()
    assert field in error.value.message_dict, error.value.message_dict


def test_a_free_event_has_no_price() -> None:
    page = EventPageFactory.create(located=True)
    _refused(page, "price", is_free=True, price=Decimal("5.00"))


def test_a_price_is_not_negative() -> None:
    page = EventPageFactory.create(located=True)
    _refused(page, "price", price=Decimal("-1.00"))


def test_the_ticket_link_is_https() -> None:
    page = EventPageFactory.create(located=True)
    _refused(page, "ticket_url", ticket_url="http://tickets.example.com/fair")


def test_a_previous_start_needs_the_postponed_state() -> None:
    page = EventPageFactory.create(located=True)
    _refused(page, "previous_start", previous_start=page.starts_at)


def test_the_editor_panels_carry_the_new_fields() -> None:
    names = {
        getattr(panel, "field_name", None)
        for group in EventPage.content_panels
        for panel in [group, *getattr(group, "children", [])]
    }
    assert {"ticket_url", "price", "is_free", "postponed", "previous_start"} <= names


def test_the_services_save_the_new_fields() -> None:
    primary_index()
    assert {"ticket_url", "price", "is_free", "postponed", "previous_start"} <= set(
        services.EDITABLE_FIELDS
    )


@pytest.mark.django_db(transaction=True)
def test_the_migration_applies_and_reverses() -> None:
    def columns() -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, TABLE)
        return {column.name for column in description}

    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes("events")
    assert leaf[0][1] != PREVIOUS
    try:
        MigrationExecutor(connection).migrate([("events", PREVIOUS)])
        assert "ticket_url" not in columns()
    finally:
        MigrationExecutor(connection).migrate(leaf)
    assert set(NEW_FIELDS) <= columns()
