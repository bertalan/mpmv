"""Validation rules for the translation review files (I18N.13), pure functions."""

import pytest

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
        for token in ("%(name)s", "{name}", "%d", "<b>", "Notes", "Max length"):
            assert token in joined


def test_read_me_names_the_tone_per_language() -> None:
    joined = " ".join(engine.read_me_lines("en", "de"))
    assert "du" in joined
    assert "tu" in " ".join(engine.read_me_lines("en", "fr"))
    assert "tú" in " ".join(engine.read_me_lines("en", "es"))


def test_banner_is_short() -> None:
    assert len(engine.banner("en", "de")) < 300


# --- hardened rules (review of #305) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "ok"),
    [
        ("%(price)08.2f total", "total %(price)08.2f"),
        ("%.2f and %5s", "%5s und %.2f"),
        ("100%% sure %s", "%s: 100%% sicher"),
        ("{0:.2f} and {name!r}", "{name!r} {0:.2f}"),
        ("{{ user.name }} and {% url 'x' %}", "{% url 'x' %} {{ user.name }}"),
    ],
)
def test_placeholders_of_every_syntax_are_compared_exactly(source: str, ok: str) -> None:
    assert _broken(source, "x", ok) == []
    assert _broken(source, "x", "plain") == [Rule.PLACEHOLDERS]


@pytest.mark.parametrize(
    ("source", "bad"),
    [
        ("%(price)08.2f", "%(price)8.2f"),
        ("%.2f", "%.3f"),
        ("%s", "%r"),
        ("100%%", "100%"),
        ("{0:.2f}", "{0:.3f}"),
        ("{name!r}", "{name!s}"),
        ("{name}", "{name} {name}"),
        ("{{ user.name }}", "{{ user.email }}"),
        ("{% url 'x' %}", "{% url 'y' %}"),
    ],
)
def test_a_changed_placeholder_is_refused(source: str, bad: str) -> None:
    assert Rule.PLACEHOLDERS in _broken(source, "x", bad)


@pytest.mark.parametrize("bad", ["{name.__class__}", "{name[0]}", "{0.x}", "{name.__globals__}"])
def test_attribute_and_index_access_cannot_be_introduced(bad: str) -> None:
    assert Rule.PLACEHOLDERS in _broken("Hi {name} {0}", "x", bad + " {0}")


@pytest.mark.parametrize(
    "text",
    [
        "see HTTP://evil.example",
        "see https://evil.example",
        "ftp://evil.example/x",
        "mail mailto:a@evil.example",
        '<a href="JavaScript:alert(1)">x</a>',
        "data:text/html;base64,AAAA",
        "go to //evil.example/x",
        "visit WWW.evil.example",
        "java\tscript:alert(1)",
        "&#104;ttp://evil.example",
    ],
)
def test_every_kind_of_new_link_is_refused(text: str) -> None:
    assert Rule.NEW_URL in _broken("Plain", "x", text)


def test_a_link_already_in_the_source_is_kept() -> None:
    src = '<a href="https://example.com/a">x</a>'
    assert _broken(src, "x", '<a href="https://example.com/a">y</a>') == []


@pytest.mark.parametrize(
    "bad",
    [
        "<b>hi",  # unclosed
        "<b>hi</b",  # broken end tag
        "<b class='x'>hi</b>",  # new attribute
        '<b onclick="x()">hi</b>',  # event handler
        "<B>hi</B><i>",  # extra tag
    ],
)
def test_html_is_parsed_and_must_match_tags_and_attributes(bad: str) -> None:
    assert Rule.HTML_TAGS in _broken("<b>hi</b>", "x", bad)


def test_html_attribute_values_must_match_and_case_or_order_is_free() -> None:
    src = '<a href="/a" title="T">x</a>'
    assert _broken(src, "x", '<A title="T" href="/a">y</A>') == []
    assert Rule.HTML_TAGS in _broken(src, "x", '<a href="/b" title="T">y</a>')


def test_the_rules_for_formulas_and_duplicates_have_texts_in_every_language() -> None:
    for lang in LANGUAGES:
        assert engine.rule_text(Rule.FORMULA, lang)
        assert engine.rule_text(Rule.DUPLICATE, lang)


# --- second review of #305 ---------------------------------------------------------------------


def test_a_nested_format_field_cannot_hide_attribute_access() -> None:
    src = "{0!s:>{1}} items"
    assert _broken(src, "x", "{0!s:>{1}} Artikel") == []
    assert Rule.PLACEHOLDERS in _broken(src, "x", "{0.__globals__!s:>{1}} Artikel")
    assert Rule.PLACEHOLDERS in _broken(src, "x", "{0!s:>{1.x}} Artikel")
    assert Rule.PLACEHOLDERS in _broken(src, "x", "{0!r:>{1}} Artikel")
    assert Rule.PLACEHOLDERS in _broken(src, "x", "{0!s:<{1}} Artikel")
    assert Rule.PLACEHOLDERS in _broken(src, "x", "{0!s:>{2}} Artikel")


@pytest.mark.parametrize("bad", ["{0!s:>{1} items", "0!s:>{1}} items", "{0 items", "items }"])
def test_unbalanced_braces_are_refused_when_the_source_is_well_formed(bad: str) -> None:
    assert Rule.PLACEHOLDERS in _broken("{0} items", "x", bad)


def test_a_lone_brace_that_is_in_the_source_too_is_not_a_new_problem() -> None:
    assert _broken("Use { to start", "x", "Mit { starten") == []


def test_index_access_in_a_nested_spec_is_refused() -> None:
    assert Rule.PLACEHOLDERS in _broken("{0:{1}}", "x", "{0:{1[0]}}")


@pytest.mark.parametrize(
    "text", ["file:///etc/passwd", "call tel:+390000", "sms:123", "VBScript:x", "FILE:x"]
)
def test_more_schemes_are_refused(text: str) -> None:
    assert Rule.NEW_URL in _broken("Plain", "x", text)


def test_the_printf_space_flag_is_part_of_the_placeholder() -> None:
    assert Rule.PLACEHOLDERS in _broken("% d items", "x", "%d Artikel")
    assert _broken("% d items", "x", "% d Artikel") == []
