"""EVT.3: the pure moderation, sequence and content rules of `events.engine` (spec 15).

- `needs_review`: the threshold rule of "Moderation with Wagtail workflows" (regression cases
  1-3, the decision part).
- `next_sequence`: the ICS SEQUENCE grows when a published revision changes a field the ICS
  shows, and stays on a no-op publish.
- `refused_blocks` and `has_unsafe_link`: the vendor-safe body rules.
- `event_slug`: a slug from the title, a suffix on a collision, never an index route name.
"""

import datetime as dt
from decimal import Decimal

import pytest

from apps.events import engine
from apps.events.engine import (
    ALWAYS_REVIEW,
    DEFAULT_MODE,
    ICS_FIELDS,
    TRUSTED,
    event_slug,
    has_unsafe_link,
    needs_review,
    next_sequence,
    refused_blocks,
)

# --- needs_review


@pytest.mark.parametrize(
    ("approved", "threshold", "expected"),
    [(0, 3, True), (1, 3, True), (2, 3, True), (3, 3, False), (4, 3, False), (3, 5, True)],
)
def test_the_default_mode_reviews_a_vendors_first_events(
    approved: int, threshold: int, expected: bool
) -> None:
    assert needs_review(approved, threshold, DEFAULT_MODE, is_staff=False) is expected


def test_a_zero_threshold_never_reviews() -> None:
    assert needs_review(0, 0, DEFAULT_MODE, is_staff=False) is False


def test_always_review_and_trusted_override_the_threshold() -> None:
    assert needs_review(100, 3, ALWAYS_REVIEW, is_staff=False) is True
    assert needs_review(0, 3, TRUSTED, is_staff=False) is False


@pytest.mark.parametrize("mode", [DEFAULT_MODE, ALWAYS_REVIEW, TRUSTED])
def test_staff_and_platform_events_are_never_reviewed(mode: str) -> None:
    assert needs_review(0, 3, mode, is_staff=True) is False


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="mode"):
        needs_review(0, 3, "sometimes", is_staff=False)


# --- next_sequence

START = dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC)


def _snapshot(**changes: object) -> dict[str, object]:
    base: dict[str, object] = dict.fromkeys(ICS_FIELDS, "")
    base.update(
        title="Spring fair",
        summary="Meet the makers.",
        all_day=False,
        starts_at=START,
        ends_at=START + dt.timedelta(hours=8),
        start_date=None,
        end_date=None,
        timezone="Europe/Rome",
        exdates=[],
        exdate_days=[],
        latitude=None,
        longitude=None,
        cancelled_at=None,
    )
    base.update(changes)
    return base


def test_a_date_change_bumps_the_sequence() -> None:
    before = _snapshot()
    after = _snapshot(starts_at=START + dt.timedelta(days=1), ends_at=START + dt.timedelta(days=1))
    assert next_sequence(4, before, after, was_published=True) == 5


def test_a_no_op_publish_keeps_the_sequence() -> None:
    assert next_sequence(4, _snapshot(), _snapshot(), was_published=True) == 4


def test_a_change_outside_the_ics_fields_keeps_the_sequence() -> None:
    before, after = _snapshot(), _snapshot()
    before["body"], after["body"] = "old", "new"
    assert next_sequence(2, before, after, was_published=True) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Autumn fair"),
        ("summary", "Another text."),
        ("timezone", "Europe/Berlin"),
        ("rrule", "FREQ=YEARLY;COUNT=3"),
        ("exdates", [START]),
        ("city", "Torino"),
        ("latitude", Decimal("45.000000")),
        ("cancelled_at", START),
    ],
)
def test_every_field_the_ics_shows_bumps_the_sequence(field: str, value: object) -> None:
    assert next_sequence(0, _snapshot(), _snapshot(**{field: value}), was_published=True) == 1


def test_the_first_publication_keeps_the_sequence() -> None:
    """Nobody has seen the event in a calendar yet."""
    before = _snapshot(title="Draft title")
    assert next_sequence(0, before, _snapshot(), was_published=False) == 0


def test_the_ics_fields_cover_the_spec_list() -> None:
    for name in ("starts_at", "start_date", "timezone", "rrule", "exdates", "title", "summary"):
        assert name in ICS_FIELDS
    for name in ("street", "city", "country", "latitude", "longitude", "cancelled_at"):
        assert name in ICS_FIELDS


# --- vendor-safe body


def test_vendors_get_the_vendor_safe_blocks_only() -> None:
    assert refused_blocks(["paragraph", "figure", "gallery", "cta", "faq"], is_staff=False) == []
    assert refused_blocks(["paragraph", "embed", "raw_html"], is_staff=False) == [
        "embed",
        "raw_html",
    ]


def test_vendors_get_the_product_blocks_checked_against_their_own_products() -> None:
    # Cards are checked with `product_ids`, a grid with `grid_sources` (services).
    assert refused_blocks(["product_cards", "product_grid"], is_staff=False) == []
    assert refused_blocks(["countdown"], is_staff=False) == ["countdown"]


def test_the_grid_sources_of_a_raw_body() -> None:
    raw = [
        {"type": "product_grid", "value": {"source": [{"type": "vendor", "value": 7}]}},
        {"type": "product_grid", "value": {"source": [{"type": "brand", "value": 2}]}},
        {"type": "product_grid", "value": {"source": []}},
        {"type": "product_grid", "value": {"source": [{"type": "vendor", "value": 1}] * 2}},
        {"type": "product_grid", "value": "nope"},
        {"type": "paragraph", "value": "<p>x</p>"},
    ]
    assert engine.grid_sources(raw) == [
        ("vendor", 7),
        ("brand", 2),
        (None, None),
        (None, None),
        (None, None),
    ]


def test_the_product_ids_of_a_raw_body_come_from_the_cards_blocks_only() -> None:
    raw = [
        {"type": "product_cards", "value": {"heading": "", "products": [3, {"value": 4}]}},
        {"type": "product_cards", "value": {"products": [{"type": "item", "value": 5, "id": "x"}]}},
        {"type": "paragraph", "value": "<p>9</p>"},
        {"type": "countdown", "value": {"product": 8}},
    ]
    assert engine.product_ids(raw) == [3, 4, 5]


def test_a_malformed_cards_value_gives_no_ids_to_trust() -> None:
    raw = [{"type": "product_cards", "value": "nope"}, {"type": "product_cards", "value": {}}]
    assert engine.product_ids(raw) == []


def test_staff_get_every_block() -> None:
    assert refused_blocks(["embed", "raw_html"], is_staff=True) == []


@pytest.mark.parametrize(
    "html",
    [
        '<a href="javascript:alert(1)">x</a>',
        "<a href='JavaScript:alert(1)'>x</a>",
        '<a href=" data:text/html;base64,AAAA">x</a>',
        '<a href="vbscript:msgbox(1)">x</a>',
        '<a href="java&#x09;script:alert(1)">x</a>',
    ],
)
def test_script_and_data_links_are_unsafe(html: str) -> None:
    assert has_unsafe_link(html)


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://example.com/">x</a>',
        '<a href="/it/shop/">x</a>',
        '<a href="mailto:info@example.com">x</a>',
        "<p>Data: javascript is fun</p>",
    ],
)
def test_ordinary_links_are_safe(html: str) -> None:
    assert not has_unsafe_link(html)


# --- slugs


def test_the_slug_comes_from_the_title() -> None:
    assert event_slug("Spring fair 2027", taken=set()) == "spring-fair-2027"


def test_a_taken_slug_gets_a_suffix() -> None:
    assert event_slug("Spring fair", taken={"spring-fair", "spring-fair-2"}) == "spring-fair-3"


@pytest.mark.parametrize("title", ["Calendar", "Past", "Vendor", "Type", "feed.rss"])
def test_a_route_name_of_the_index_is_never_a_slug(title: str) -> None:
    slug = event_slug(title, taken=set())
    assert slug not in engine.RESERVED_SLUGS
    assert slug.startswith(title.lower().split(".")[0])


def test_an_empty_title_still_gets_a_slug() -> None:
    assert event_slug("!!!", taken=set()) == "event"
