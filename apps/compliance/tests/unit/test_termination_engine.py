"""CMP.5 part 4: pure rules for the 30 days' notice of termination (P2B art. 4)."""

import datetime as dt

from apps.compliance import engine

ISSUED = dt.datetime(2026, 3, 1, 10, 0, tzinfo=dt.UTC)


def problems(**override: object) -> list[str]:
    fields: dict[str, object] = {
        "facts": "The storefront sold a banned article again.",
        "ground": "Platform terms, section on prohibited goods",
        "exception": "",
        "issued_at": ISSUED,
        "effective_at": ISSUED + dt.timedelta(days=30),
    }
    fields.update(override)
    return engine.termination_problems(**fields)  # type: ignore[arg-type]


def test_the_minimum_notice_is_thirty_days() -> None:
    assert engine.MIN_NOTICE_DAYS == 30


def test_a_configured_period_never_goes_below_thirty_days() -> None:
    assert engine.notice_days(None) == 30
    assert engine.notice_days(7) == 30
    assert engine.notice_days(0) == 30
    assert engine.notice_days(45) == 45


def test_the_default_effective_time_is_the_notice_period_after_the_notice() -> None:
    assert engine.default_effective_at(ISSUED, 30) == ISSUED + dt.timedelta(days=30)
    assert engine.default_effective_at(ISSUED, 5) == ISSUED + dt.timedelta(days=30)


def test_a_complete_notice_with_thirty_days_has_no_problem() -> None:
    assert problems() == []


def test_a_date_one_second_short_of_thirty_days_is_refused() -> None:
    short = ISSUED + dt.timedelta(days=30) - dt.timedelta(seconds=1)
    assert problems(effective_at=short) == ["effective_at"]


def test_a_longer_notice_is_fine() -> None:
    assert problems(effective_at=ISSUED + dt.timedelta(days=90)) == []


def test_a_closed_list_of_exceptions_allows_a_shorter_notice() -> None:
    assert engine.TERMINATION_EXCEPTIONS == (
        "legal_obligation",
        "repeated_infringement",
        "illegal_content",
    )
    for exception in engine.TERMINATION_EXCEPTIONS:
        assert problems(exception=exception, effective_at=ISSUED) == []


def test_an_unknown_exception_is_refused_and_gives_no_shorter_notice() -> None:
    assert problems(exception="cheaper", effective_at=ISSUED) == ["exception", "effective_at"]


def test_even_an_exception_cannot_end_the_service_in_the_past() -> None:
    assert problems(exception="illegal_content", effective_at=ISSUED - dt.timedelta(hours=1)) == [
        "effective_at"
    ]


def test_facts_and_ground_are_required() -> None:
    assert problems(facts=" ") == ["facts"]
    assert problems(ground="") == ["ground"]


def test_a_termination_is_due_at_its_effective_time_and_never_before() -> None:
    effective = ISSUED + dt.timedelta(days=30)
    assert not engine.termination_due(effective, now=effective - dt.timedelta(seconds=1))
    assert engine.termination_due(effective, now=effective)
    assert engine.termination_due(effective, now=effective + dt.timedelta(days=3))


def test_the_transparency_payload_of_a_termination_says_terminated() -> None:
    payload = engine.transparency_payload(
        restriction=engine.RESTRICTION_TERMINATE,
        ground_kind=engine.GROUND_TERMS,
        target_kind="vendor",
        explanation="Seller account terminated.",
        based_on_notice=False,
        automated_detection=False,
        automated_decision=False,
        applied_on=ISSUED.date(),
        valid_until=None,
        reference="ref",
    )
    assert payload["decision_account"] == "DECISION_ACCOUNT_TERMINATED"
    assert "end_date_account_restriction" not in payload
