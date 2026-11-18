"""SEO.15: the public seller name is never the legal name of a natural person."""

from apps.seo import engine, schema_check


def facts(**kwargs: object) -> engine.VendorNameFacts:
    return engine.VendorNameFacts(**{"display_name": "Rossi Ceramics", **kwargs})  # type: ignore[arg-type]


def test_a_sole_trader_with_a_shop_name_emits_the_shop_name() -> None:
    sole = facts(
        legal_name="Mario Rossi", first_name="Mario", last_name="Rossi", natural_person=True
    )
    assert engine.public_seller_name(sole) == "Rossi Ceramics"


def test_a_sole_trader_whose_only_name_is_the_legal_name_has_no_seller() -> None:
    sole = facts(
        display_name="Mario Rossi",
        legal_name="Mario Rossi",
        first_name="Mario",
        last_name="Rossi",
        natural_person=True,
    )
    assert engine.public_seller_name(sole) is None


def test_the_comparison_ignores_case_spacing_punctuation_and_name_order() -> None:
    for shown in ("  mario   ROSSI ", "Rossi, Mario", "Mario Rossi."):
        sole = facts(
            display_name=shown,
            legal_name="Mario Rossi",
            first_name="Mario",
            last_name="Rossi",
            natural_person=True,
        )
        assert engine.public_seller_name(sole) is None, shown


def test_a_company_keeps_its_name_even_when_it_equals_the_legal_name() -> None:
    company = facts(display_name="Rossi S.r.l.", legal_name="Rossi S.r.l.")
    assert engine.public_seller_name(company) == "Rossi S.r.l."


def test_an_empty_name_is_no_seller() -> None:
    assert engine.public_seller_name(facts(display_name="  ")) is None


def graph(name: str) -> dict[str, object]:
    return {
        "@graph": [{"@type": "Organization", "@id": "https://x.test/v#organization", "name": name}]
    }


def test_the_schema_check_rejects_a_seller_equal_to_a_natural_persons_name() -> None:
    people = engine.natural_person_names(
        facts(legal_name="Mario Rossi", first_name="Mario", last_name="Rossi", natural_person=True)
    )
    errors = schema_check.rich_result_errors(graph("Mario Rossi"), person_names=people)
    assert any("natural person" in error for error in errors)
    assert schema_check.rich_result_errors(graph("Rossi Ceramics"), person_names=people) == []
    assert schema_check.rich_result_errors(graph("Mario Rossi")) == []
