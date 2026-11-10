"""CMP.5 part 3: pure rules for the DSA article 17 statement of reasons and the article 24(5)
transparency database payload."""

import datetime as dt

from apps.compliance import engine


def problems(**override: object) -> list[str]:
    fields: dict[str, object] = {
        "restriction": engine.RESTRICTION_REMOVE,
        "scope": "All languages, until restored",
        "facts": "The listing offers a counterfeit item.",
        "ground_kind": engine.GROUND_ILLEGAL,
        "ground": "Trade mark infringement",
    }
    fields.update(override)
    return engine.statement_problems(**fields)  # type: ignore[arg-type]


def test_a_complete_statement_has_no_problem() -> None:
    assert problems() == []


def test_every_part_of_article_17_3_is_required() -> None:
    for field in ("scope", "facts", "ground"):
        assert problems(**{field: "  "}) == [field]


def test_the_restriction_and_the_ground_kind_are_known_values() -> None:
    assert problems(restriction="delete_all") == ["restriction"]
    assert problems(ground_kind="because") == ["ground_kind"]


def test_the_payload_names_the_decision_and_holds_no_vendor_identity() -> None:
    payload = engine.transparency_payload(
        restriction=engine.RESTRICTION_REMOVE,
        scope="All languages",
        facts="Counterfeit item",
        ground_kind=engine.GROUND_ILLEGAL,
        ground="Trade mark infringement",
        target_kind="product",
        based_on_notice=True,
        automated_detection=False,
        automated_decision=False,
        applied_on=dt.date(2027, 3, 4),
        reference="abc",
    )
    assert payload["decision_visibility"] == "DECISION_VISIBILITY_CONTENT_REMOVED"
    assert payload["source_type"] == "SOURCE_ARTICLE_16"
    assert payload["illegal_content_legal_ground"] == "Trade mark infringement"
    assert "incompatible_content_ground" not in payload
    assert payload["application_date"] == "2027-03-04"
    assert payload["automated_detection"] == "No"
    assert payload["puid"] == "abc"
    assert not {"vendor", "email", "name", "url"} & set(payload)


def test_a_terms_ground_and_an_own_initiative_decision() -> None:
    payload = engine.transparency_payload(
        restriction=engine.RESTRICTION_SUSPEND,
        scope="The whole storefront",
        facts="Repeated misleading offers",
        ground_kind=engine.GROUND_TERMS,
        ground="Platform terms, restrictions section",
        target_kind="vendor",
        based_on_notice=False,
        automated_detection=True,
        automated_decision=False,
        applied_on=dt.date(2027, 3, 4),
        reference="x",
    )
    assert payload["decision_account"] == "DECISION_ACCOUNT_SUSPENDED"
    assert payload["source_type"] == "SOURCE_VOLUNTARY"
    assert payload["incompatible_content_ground"] == "Platform terms, restrictions section"
    assert "illegal_content_legal_ground" not in payload
    assert payload["automated_detection"] == "Yes"
