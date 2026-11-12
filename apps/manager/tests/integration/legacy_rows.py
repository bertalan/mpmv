"""Fixture input for the MIG.21 export: plain dicts, as the MIG.1 extractor would yield them."""

from collections.abc import Iterable, Mapping
from typing import Any

LEGACY_A = 7001
LEGACY_B = 7002
SOURCE = "apps.manager.tests.integration.legacy_rows.fixture_rows"

ALL: list[Mapping[str, Any]] = [
    {
        "kind": "store_review",
        "legacy_vendor_id": LEGACY_A,
        "date": "2024-03-05",
        "rating": 5,
        "author_name": "Giulia Verdi",
        "author_email": "giulia@example.com",
        "subject": "Fast",
        "message": "mine-1 arrived in two days",
        "reply": "",
        "product": "",
    },
    {
        "kind": "enquiry",
        "legacy_vendor_id": LEGACY_A,
        "date": "2024-04-01",
        "rating": None,
        "author_name": "Luca Neri",
        "author_email": "luca@example.com",
        "subject": "Fitment",
        "message": '=HYPERLINK("http://x.example","mine-2")',
        "reply": "Yes, it fits",
        "product": "SKU-1",
    },
    {
        "kind": "store_review",
        "legacy_vendor_id": LEGACY_B,
        "date": "2024-03-09",
        "rating": 1,
        "author_name": "Other Person",
        "author_email": "other@example.com",
        "subject": "Slow",
        "message": "theirs a very long wait",
        "reply": "",
        "product": "",
    },
    {
        "kind": "enquiry",
        "legacy_vendor_id": 9999,
        "date": "2024-04-02",
        "rating": None,
        "author_name": "Nobody Known",
        "author_email": "nobody@example.com",
        "subject": "Orphan",
        "message": "theirs orphan",
        "reply": "",
        "product": "",
    },
]


def rows(legacy_vendor_id: int) -> list[Mapping[str, Any]]:
    return [r for r in ALL if r["legacy_vendor_id"] == legacy_vendor_id]


def fixture_rows() -> Iterable[Mapping[str, Any]]:
    return ALL
