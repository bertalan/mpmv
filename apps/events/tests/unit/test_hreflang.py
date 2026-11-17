"""EVT.8: the `hreflang` set of an event page (spec 15 "One URL per existing translation") is
pure: existing translations only, `x-default` on the EN translation or else the original."""

from apps.events import engine

ORDER = ("en", "it", "de", "fr", "es")


def test_only_the_original_gets_x_default_on_itself() -> None:
    urls = {"de": "https://x/de/events/a/"}
    assert engine.event_hreflang(urls, "de", ORDER) == [
        ("de", "https://x/de/events/a/"),
        ("x-default", "https://x/de/events/a/"),
    ]


def test_x_default_is_the_english_translation_when_there_is_one() -> None:
    urls = {
        "de": "https://x/de/events/a/",
        "en": "https://x/events/a/",
        "it": "https://x/it/events/a/",
    }
    assert engine.event_hreflang(urls, "de", ORDER) == [
        ("en", "https://x/events/a/"),
        ("it", "https://x/it/events/a/"),
        ("de", "https://x/de/events/a/"),
        ("x-default", "https://x/events/a/"),
    ]


def test_x_default_is_the_original_without_an_english_translation() -> None:
    urls = {"de": "https://x/de/events/a/", "fr": "https://x/fr/events/a/"}
    result = engine.event_hreflang(urls, "de", ORDER)
    assert result[-1] == ("x-default", "https://x/de/events/a/")
    assert [code for code, _url in result] == ["de", "fr", "x-default"]


def test_an_unpublished_original_leaves_x_default_on_the_first_language_present() -> None:
    urls = {"fr": "https://x/fr/events/a/", "it": "https://x/it/events/a/"}
    result = engine.event_hreflang(urls, "de", ORDER)
    assert result[-1] == ("x-default", "https://x/it/events/a/")


def test_nothing_published_gives_nothing() -> None:
    assert engine.event_hreflang({}, "de", ORDER) == []
