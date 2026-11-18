"""CAT.25: the pure part of the orphan image cleanup (no database)."""

import datetime as dt

from apps.catalog import media_cleanup_engine as engine


def test_an_image_nothing_references_is_an_orphan() -> None:
    assert engine.orphan_ids([1, 2, 3], {2}) == [1, 3]


def test_orphans_keep_the_order_of_the_candidates() -> None:
    assert engine.orphan_ids([9, 4, 7], set()) == [9, 4, 7]


def test_every_image_referenced_leaves_nothing() -> None:
    assert engine.orphan_ids([1, 2], {1, 2, 5}) == []


def test_the_grace_period_protects_a_fresh_upload() -> None:
    now = dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)
    assert not engine.is_past_grace(now - dt.timedelta(hours=1), now)
    assert engine.is_past_grace(now - engine.GRACE - dt.timedelta(minutes=1), now)


def test_a_source_label_names_the_model_and_the_field() -> None:
    assert engine.source_label("catalog.productimage", "image") == "catalog.productimage.image"
