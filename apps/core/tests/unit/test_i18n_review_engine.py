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


def test_length_is_a_guide_not_a_refusal() -> None:
    assert _broken("Short", "x", "a" * 500, limit=10) == []
    assert engine.over_guide("a" * 11, 10) is True
    assert engine.over_guide("a" * 10, 10) is False


def test_several_rules_are_all_reported_in_a_stable_order() -> None:
    got = _broken("<b>%s</b>", "x", "https://a.example/ " * 3, limit=5)
    assert got == [Rule.PLACEHOLDERS, Rule.HTML_TAGS, Rule.NEW_URL]


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


LANGUAGES = ("en", "it", "de", "fr", "es")


def test_each_of_the_five_languages_has_its_own_read_me() -> None:
    texts = {}
    for lang in LANGUAGES:
        assert engine.read_me_language(lang) == lang
        texts[lang] = engine.read_me_lines(lang, "de")
    assert len({tuple(t) for t in texts.values()}) == 5
    for lang, lines in texts.items():
        assert len(lines) == len(texts["en"])
        for rule in Rule:
            assert engine.rule_text(rule, lang) in lines
            assert engine.rule_text(rule, lang) != engine.rule_text(rule, "en") or lang == "en"
        assert engine.banner(lang, "de") != engine.banner("en", "de") or lang == "en"


def test_read_me_language_falls_back_to_english_for_others() -> None:
    assert engine.read_me_language("pt") == "en"


def test_read_me_keeps_the_technical_examples_in_every_language() -> None:
    for lang in LANGUAGES:
        joined = " ".join(engine.read_me_lines(lang, "de"))
        for token in ("%(name)s", "{name}", "%s", "<b>", "Notes", "Max length"):
            assert token in joined


def test_read_me_names_the_tone_per_language() -> None:
    joined = " ".join(engine.read_me_lines("en", "de"))
    assert "du" in joined
    assert "tu" in " ".join(engine.read_me_lines("en", "fr"))
    assert "tú" in " ".join(engine.read_me_lines("en", "es"))


def test_banner_is_short() -> None:
    assert len(engine.banner("en", "de")) < 300
