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


def test_only_allow_listed_apps_are_checked() -> None:
    found = {
        ("Your catalog", None): (None, ["apps/manager/x.html"]),
        ("Your color", None): (None, ["apps/fitment/x.html"]),
    }
    assert len(british.violations(found)) == 1


def test_exception_keeps_the_role_notice() -> None:
    msgid = "You can view this section; changes need the Catalog role."
    assert british.violations({(msgid, None): (None, ["apps/manager/list.html"])}) == []


def test_swept_apps_are_clean_now() -> None:
    assert british.check() == 0


def test_the_scope_matches_whole_app_directories_only() -> None:
    """`apps/core_x` or `apps/api_v2` is not swept by accident (review of #960)."""
    assert british.in_scope(["apps/core/templates/x.html:3"])
    assert not british.in_scope(["apps/core_x/templates/x.html:3"])
    assert not british.in_scope(["apps/api_v2/views.py:9"])


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
