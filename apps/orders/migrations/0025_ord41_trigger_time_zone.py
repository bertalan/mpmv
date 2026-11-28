"""ORD.41: the order guard reads the business time zone instead of a frozen one.

The guard of migration 0010 (still the current `orders_order_guard`) computed "today", the end
of the tenth fiscal year and the end of the active phase in a zone frozen in its SQL. This
migration installs the same function with each of the three reading `business_time_zone_at()`
(core 0036), so the database guards and the Python dates agree:

- "today" and the active phase (24 months) in the zone valid now, as `_scrub` does with
  `business_tz()`;
- `tax_retention_until` from the order's own date in the zone valid then, as `Order.save` does
  with `business_tz(created_at)`.

Nothing else in the function changes. Reverse restores the 0010 function, frozen zone included
(the historical migrations keep their SQL).
"""

import importlib
import re

from django.db import migrations

_previous = importlib.import_module("apps.orders.migrations.0010_paid_at_guards")

_FROZEN = r" AT TIME ZONE '[A-Za-z_/]+'"
_REWRITES = (
    (r"now\(\)" + _FROZEN, "now() AT TIME ZONE business_time_zone_at(now())"),
    (
        r"NEW\.created_at" + _FROZEN,
        "NEW.created_at AT TIME ZONE business_time_zone_at(NEW.created_at)",
    ),
    (r"OLD\.created_at" + _FROZEN, "OLD.created_at AT TIME ZONE business_time_zone_at(now())"),
)


def _guard_0010() -> str:
    return str(_previous.FUNCTION).replace("__ARROWS__", str(_previous._arrows))


def _reading_the_operator(sql: str) -> str:
    for pattern, replacement in _REWRITES:
        sql, count = re.subn(pattern, replacement, sql)
        if count != 1:
            raise RuntimeError(f"0010 guard: expected one {pattern!r}, found {count}")
    if "AT TIME ZONE '" in sql:
        raise RuntimeError("0010 guard: a frozen time zone is left")
    return sql


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_business_time_zone_at"),
        ("orders", "0024_ven20_expected_dispatch_on"),
    ]

    operations = [
        migrations.RunSQL(_reading_the_operator(_guard_0010()), reverse_sql=_guard_0010())
    ]
