"""I18N.9: the effective date of a synced legal translation (Q-42, CMS.5 versions).

A translation keeps its own effective date, but a sync must not publish a newer source text
under the translation's older date: under CMS.5 that would make the new text apply
retroactively and record the old text as corrected, not superseded. Pure rules, no database.
"""

import datetime as dt

from apps.cms.engine import synced_translation_date, translation_date_problem

JAN = dt.date(2026, 1, 1)
MAR = dt.date(2026, 3, 1)
DEC = dt.date(2026, 12, 1)


def test_a_sync_that_brings_a_newer_source_text_moves_the_date_to_the_sources() -> None:
    assert synced_translation_date(own=JAN, source=DEC, text_changed=True) == DEC


def test_a_translation_keeps_its_own_later_date_through_a_sync() -> None:
    assert synced_translation_date(own=MAR, source=JAN, text_changed=True) == MAR


def test_a_sync_that_changes_no_text_keeps_the_date() -> None:
    assert synced_translation_date(own=JAN, source=DEC, text_changed=False) == JAN


def test_a_translation_without_a_synced_source_keeps_its_date() -> None:
    assert synced_translation_date(own=JAN, source=None, text_changed=True) == JAN


def test_a_translators_date_may_not_precede_the_source_or_the_last_version() -> None:
    assert translation_date_problem(MAR, source=JAN, latest=JAN) is None
    assert translation_date_problem(MAR, source=None, latest=None) is None
    assert translation_date_problem(JAN, source=MAR, latest=None) == "source"
    assert translation_date_problem(JAN, source=None, latest=MAR) == "latest"
