"""CMP.5 part 1: the pure checks of the DSA points of contact and the required legal sections."""

from apps.compliance import engine


def contact_codes(**overrides: object) -> list[str]:
    values: dict[str, object] = {
        "country": "IT",
        "authority_email": "authorities@example.com",
        "authority_languages": ["it", "en"],
        "support_email": "support@example.com",
    }
    values.update(overrides)
    return [p.code for p in engine.contact_problems(**values)]  # type: ignore[arg-type]


def test_complete_contacts_have_no_problem() -> None:
    assert contact_codes() == []


def test_authority_contact_needs_an_email_and_languages() -> None:
    assert contact_codes(authority_email="") == ["authority_email"]
    assert "authority_languages" in contact_codes(authority_languages=[])


def test_authority_languages_include_an_official_language_of_the_country() -> None:
    assert contact_codes(authority_languages=["en", "fr"]) == ["authority_official_language"]
    # Belgium has three official languages: one of them is enough.
    assert contact_codes(country="BE", authority_languages=["nl", "en"]) == []


def test_authority_languages_include_english() -> None:
    # D-27 (article 11(3)): an official language and English.
    assert contact_codes(authority_languages=["it"]) == ["authority_english_language"]
    assert contact_codes(authority_languages=["fr"]) == [
        "authority_official_language",
        "authority_english_language",
    ]
    # English is both when it is official (Ireland).
    assert contact_codes(country="IE", authority_languages=["en"]) == []


def test_recipient_contact_is_the_support_email() -> None:
    assert contact_codes(support_email="") == ["support_email"]


def test_required_sections_per_kind() -> None:
    assert engine.REQUIRED_SECTIONS["platform_terms"] == (
        "restrictions",
        "moderation_policy",
        "complaint_handling",
        "redress",
    )
    assert engine.REQUIRED_SECTIONS["ranking"] == ("ranking_parameters",)


def test_missing_sections_are_reported_per_page_and_language() -> None:
    problems = engine.section_problems(
        "platform_terms", "de", ["restrictions", "moderation_policy", "redress"]
    )
    assert [(p.code, p.subject) for p in problems] == [
        ("section_missing", "platform_terms [de]: complaint_handling")
    ]
    assert engine.section_problems("ranking", "en", ["ranking_parameters"]) == []


def test_page_problem_message_names_kind_and_language() -> None:
    problem = engine.page_problem("ranking", "fr")
    assert problem.code == "page_missing"
    assert "ranking" in problem.message
    assert "fr" in problem.message


def test_regional_and_upper_case_codes_match_the_base_language() -> None:
    assert contact_codes(authority_languages=["de-AT", "EN"], country="AT") == []
    assert contact_codes(country="PT", authority_languages=["PT-BR", "en"]) == []
