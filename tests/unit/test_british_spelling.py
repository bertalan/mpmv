"""I18N.23: the English msgids of the swept apps are British English."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("i18n_british", ROOT / "tools" / "i18n_british.py")
assert _spec is not None
assert _spec.loader is not None
british = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(british)


def test_us_words_are_found_and_british_ones_pass() -> None:
    assert british.american_words("The catalog color is a favorite") == [
        "catalog",
        "color",
        "favorite",
    ]
    assert british.american_words("The catalogue colour; recognise; cancelled; licence") == []
    assert british.american_words("You are not authorized; we apologize") == [
        "authorized",
        "apologize",
    ]


def test_every_app_and_theme_is_checked() -> None:
    """I18N.25: the allow-list is every app and the theme templates."""
    found = {
        ("Your catalog", None): (None, ["apps/manager/x.html"]),
        ("Your color", None): (None, ["apps/fitment/x.html"]),
        ("Your favorite", None): (None, ["themes/ride/templates/x.html"]),
        ("Your colour", None): (None, ["apps/seo/x.html"]),
        ("Your colors", None): (None, ["tools/x.py"]),
    }
    assert len(british.violations(found)) == 3
    for app in ("cms", "seo", "events", "fitment", "trust", "ingest", "themes", "seed"):
        assert british.in_scope([f"apps/{app}/models.py:3"]), app


def test_the_role_notice_needs_no_exception() -> None:
    msgid = "You can view this section; changes need the Catalogue role."
    assert msgid not in british.EXCEPTIONS
    assert british.violations({(msgid, None): (None, ["apps/manager/list.html"])}) == []
    assert len(british.EXCEPTIONS) == 1  # only the URL name is left


def test_swept_apps_are_clean_now() -> None:
    assert british.check() == 0


def test_the_scope_is_apps_and_themes_only() -> None:
    assert british.in_scope(["apps/core/templates/x.html:3"])
    assert british.in_scope(["themes/base/templates/x.html:3"])
    assert not british.in_scope(["tools/x.py:9"])
    assert not british.in_scope(["docs/x.md:9"])


def test_i18n_24_apps_are_in_scope() -> None:
    for app in (
        "catalog",
        "cart",
        "orders",
        "payments",
        "shipping",
        "tax",
        "ledger",
        "vendors",
        "compliance",
    ):
        assert british.in_scope([f"apps/{app}/models.py:3"]), app


def test_help_markdown_scan_finds_prose_and_ignores_code(tmp_path: Path) -> None:
    """I18N.25 follow-up: a US word in help prose is found; in code, URLs, identifiers not."""
    (tmp_path / "a.md").write_text("The catalog price.\n", encoding="utf-8")
    assert british.help_violations(tmp_path) == ["a.md:1: ['catalog']"]
    (tmp_path / "a.md").write_text(
        "Use `catalog` here, see https://x.test/catalog/ and [l](/catalog/), "
        "the catalog:brand page, role Catalogue and moderation.\n\n"
        "```\ncatalog color\n```\n",
        encoding="utf-8",
    )
    assert british.help_violations(tmp_path) == []


def test_help_has_no_exceptions_left() -> None:
    assert not hasattr(british, "HELP_EXCEPTIONS")


def test_help_en_prose_is_british() -> None:
    assert british.help_violations() == []


def test_the_role_matrix_labels_are_british_english() -> None:
    """I18N.23 follow-up: SECTION_LABELS are plain strings, so the msgid sweep misses them."""
    from apps.manager import roles

    bad = {
        key: british.american_words(label)
        for key, label in roles.SECTION_LABELS.items()
        if british.american_words(label)
    }
    assert bad == {}
