"""ING.3 follow-up: constant query count for variations, latest-per-field history read."""

import datetime as dt
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.audit import AuditContext
from apps.ingest import services
from apps.ingest.models import SourceVariation
from apps.ingest.tests.factories import SourceItemFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")
T0 = dt.datetime(2026, 11, 1, 6, 0, tzinfo=dt.UTC)
T1 = T0 + dt.timedelta(days=1)


def obs(count: int, quantity: int) -> dict[str, Any]:
    kids = {
        str(100 + i): {
            "native_id": 100 + i,
            "sku": f"V-{i}",
            "stock_status": 1,
            "stock_quantity": quantity,
        }
        for i in range(count)
    }
    return {
        "kind": "variable",
        "native_id": 7,
        "sku": "BAG",
        "source_urls": [],
        "variations": kids,
    }


def queries(item: Any, observation: dict[str, Any], now: dt.datetime) -> int:
    with CaptureQueriesContext(connection) as ctx:
        services.record_source_state(item, observation, ctx=CTX, now=now)
    return len(ctx)


@pytest.mark.parametrize("second", [False, True])
def test_variation_queries_do_not_grow_with_the_number_of_variations(second: bool) -> None:
    counts = []
    for n in (1, 6):
        item = SourceItemFactory.create()
        if second:
            services.record_source_state(item, obs(n, 2), ctx=CTX, now=T0)
        counts.append(queries(item, obs(n, 3), T1))
    assert counts[0] == counts[1]


def test_bulk_write_keeps_new_and_existing_variation_behaviour() -> None:
    item = SourceItemFactory.create()
    services.record_source_state(item, obs(2, 2), ctx=CTX, now=T0)
    services.record_source_state(item, obs(3, 5), ctx=CTX, now=T1)
    rows = {v.native_id: v for v in SourceVariation.objects.filter(item=item)}
    assert sorted(rows) == [100, 101, 102]
    assert rows[100].stock_quantity == 5
    assert rows[100].last_seen_at == T1
    assert rows[102].last_seen_at == T1


def test_latest_item_fields_returns_last_flagged_value_per_field_in_one_query() -> None:
    item = SourceItemFactory.create()
    other = SourceItemFactory.create()
    services.record_item_change(item, {"price": "10.00", "title": "A"}, ctx=CTX, now=T0)
    services.record_item_change(item, {"price": "12.00", "title": "A"}, ctx=CTX, now=T1)
    services.record_item_change(item, {"price": "13.00", "title": "A", "sku": "S"}, ctx=CTX)
    services.record_item_change(other, {"price": "99"}, ctx=CTX)
    with CaptureQueriesContext(connection) as ctx:
        got = services.latest_item_fields(item.pk)
    assert len(ctx) == 1
    assert got == {"price": "13.00", "title": "A", "sku": "S"}
    assert services.latest_item_fields(item.pk, fields=["sku"]) == {"sku": "S"}
    assert services.latest_item_fields(SourceItemFactory.create().pk) == {}
    assert got == services.latest_changes([item.pk])[item.pk]
