"""CMP.5 part 3: pure rules for the DSA article 17 statement of reasons and the article 24(5)
transparency database payload."""

import datetime as dt

from apps.compliance import engine


def problems(**override: object) -> list[str]:
    fields: dict[str, object] = {
        "restriction": engine.RESTRICTION_REMOVE,
        "scope": "All languages, until restored",
        "transparency_explanation": "Counterfeit item offered.",
        "facts": "The listing offers a counterfeit item.",
        "ground_kind": engine.GROUND_ILLEGAL,
        "ground": "Trade mark infringement",
    }
    fields.update(override)
    return engine.statement_problems(**fields)  # type: ignore[arg-type]


def test_a_complete_statement_has_no_problem() -> None:
    assert problems() == []


def test_every_part_of_article_17_3_is_required() -> None:
    for field in ("scope", "facts", "ground", "transparency_explanation"):
        assert problems(**{field: "  "}) == [field]


def test_the_restriction_and_the_ground_kind_are_known_values() -> None:
    assert problems(restriction="delete_all") == ["restriction"]
    assert problems(ground_kind="because") == ["ground_kind"]


def payload(**override: object) -> dict[str, str]:
    fields: dict[str, object] = {
        "restriction": engine.RESTRICTION_REMOVE,
        "ground_kind": engine.GROUND_ILLEGAL,
        "target_kind": "product",
        "explanation": "Counterfeit item offered.",
        "based_on_notice": True,
        "automated_detection": False,
        "automated_decision": False,
        "applied_on": dt.date(2027, 3, 4),
        "valid_until": None,
        "reference": "abc",
    }
    fields.update(override)
    return engine.transparency_payload(**fields)  # type: ignore[arg-type]


def test_the_payload_names_the_decision_and_holds_no_vendor_identity() -> None:
    result = payload()
    assert result["decision_visibility"] == "DECISION_VISIBILITY_CONTENT_REMOVED"
    assert result["source_type"] == "SOURCE_ARTICLE_16"
    assert result["illegal_content_explanation"] == "Counterfeit item offered."
    assert "incompatible_content_explanation" not in result
    assert result["application_date"] == "2027-03-04"
    assert result["automated_detection"] == "No"
    assert result["puid"] == "abc"
    assert "end_date_account_restriction" not in result
    assert not {"vendor", "email", "name", "url", "facts", "ground"} & set(result)


def test_a_suspension_with_an_end_date_on_the_platform_s_own_initiative() -> None:
    result = payload(
        restriction=engine.RESTRICTION_SUSPEND,
        ground_kind=engine.GROUND_TERMS,
        based_on_notice=False,
        automated_detection=True,
        valid_until=dt.date(2027, 6, 1),
    )
    assert result["decision_account"] == "DECISION_ACCOUNT_SUSPENDED"
    assert result["end_date_account_restriction"] == "2027-06-01"
    assert result["source_type"] == "SOURCE_VOLUNTARY"
    assert result["incompatible_content_explanation"] == "Counterfeit item offered."
    assert result["automated_detection"] == "Yes"


def test_public_text_loses_personal_data_and_is_capped() -> None:
    dirty = (
        "Mail ada@example.org or see https://shop.example.com/p/1 or www.x.example/a, "
        "call +39 06 1234 5678, IBAN IT60X0542811101000000123456, card 4111 1111 1111 1111."
    )
    clean = engine.scrub_public_text(dirty)
    for leak in ("ada@", "example.org", "https://", "www.", "1234 5678", "IT60X", "4111"):
        assert leak not in clean
    assert len(engine.scrub_public_text("a" * 5000)) == engine.PUBLIC_TEXT_LIMIT
