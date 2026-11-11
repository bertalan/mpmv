"""Validation rules for the translation review files (I18N.13), pure functions."""

from apps.core.i18n_review import engine
from apps.core.i18n_review.engine import Rule


def _broken(source: str, current: str, reviewer: str, limit: int = 200) -> list[Rule]:
    return engine.validate(source, current, reviewer, limit)


def test_a_clean_edit_passes() -> None:
    assert _broken("Hello %(name)s", "Ciao %(name)s", "Salve %(name)s") == []


def test_placeholders_must_match_in_every_syntax() -> None:
    assert _broken("Hi %(name)s", "x", "Hi %(nome)s") == [Rule.PLACEHOLDERS]
    assert _broken("Hi {name}", "x", "Hi") == [Rule.PLACEHOLDERS]
    assert _broken("Total %s", "x", "Totale %s %s") == [Rule.PLACEHOLDERS]
    assert _broken("%d items", "x", "%d articoli") == []
    assert _broken("%(a)s and %(b)s", "x", "%(b)s e %(a)s") == []  # order may change


def test_html_tags_must_match() -> None:
    assert _broken("Click <b>here</b>", "x", "Clicca <b>qui") == [Rule.HTML_TAGS]
    assert _broken("Click <b>here</b>", "x", "Clicca <i>qui</i>") == [Rule.HTML_TAGS]
    assert _broken("Click <b>here</b>", "x", "Clicca <b>qui</b>") == []


def test_a_new_url_is_refused_but_an_existing_one_is_kept() -> None:
    src = "See https://example.com/a"
    assert _broken(src, "x", "Vedi https://example.com/a") == []
    assert _broken(src, "x", "Vedi https://example.org/b") == [Rule.NEW_URL]
    assert _broken("Plain", "x", "Vai su www.example.org") == [Rule.NEW_URL]


def test_length_limit() -> None:
    assert _broken("Short", "x", "a" * 11, limit=10) == [Rule.LENGTH]
    assert _broken("Short", "x", "a" * 10, limit=10) == []


def test_several_rules_are_all_reported_in_a_stable_order() -> None:
    got = _broken("<b>%s</b>", "x", "https://a.example/ " * 3, limit=5)
    assert got == [Rule.PLACEHOLDERS, Rule.HTML_TAGS, Rule.NEW_URL, Rule.LENGTH]


def test_checksum_changes_with_any_identifying_part() -> None:
    base = engine.checksum("ctx", "id", 0, "id", "current")
    assert base == engine.checksum("ctx", "id", 0, "id", "current")
    assert base != engine.checksum("ctx", "id2", 0, "id", "current")
    assert base != engine.checksum("", "id", 0, "id", "current")
    assert base != engine.checksum("ctx", "id", 1, "id", "current")
    assert base != engine.checksum("ctx", "id", 0, "id", "other")


def test_rule_text_and_read_me_use_the_same_words() -> None:
    for lang in ("en", "it"):
        lines = engine.read_me_lines(lang, "de")
        for rule in Rule:
            assert engine.rule_text(rule, lang) in lines


def test_read_me_language_falls_back_to_english() -> None:
    assert engine.read_me_language("it") == "it"
    assert engine.read_me_language("fr") == "en"
    assert engine.read_me_language("de") == "en"


def test_read_me_names_the_tone_per_language() -> None:
    joined = " ".join(engine.read_me_lines("en", "de"))
    assert "du" in joined
    assert "tu" in " ".join(engine.read_me_lines("en", "fr"))
    assert "tú" in " ".join(engine.read_me_lines("en", "es"))


def test_banner_is_short() -> None:
    assert len(engine.banner("en", "de")) < 300
