"""THM.8 (2/2): the `ride` stylesheet of the customer area and the sign-in pages (spec 14 §14.6).

The account pages extend `layouts/account.html` and use only base components, so `ride` styles
them from one extra stylesheet. Its rules sit in the `overrides` layer, use colours only through
tokens, and the reduced-motion and 44 px rules of the other ride files hold here too.
"""

import re

import pytest

from apps.themes.build import read_css_sources
from apps.themes.tests.unit.test_ride_theme import RAW_COLOUR, RIDE, STATIC

CSS = STATIC / "css" / "50-account-auth.css"

HOOKS = [
    ".account-page",
    ".timeline",
    ".recovery-codes",
    ".privacy-account",
    ".privacy-erased",
    ".session-level",
]


@pytest.fixture(scope="module")
def source() -> str:
    assert CSS.is_file(), f"{CSS} is missing"
    return CSS.read_text(encoding="utf-8")


def test_the_account_stylesheet_is_part_of_the_ride_build(source: str) -> None:
    assert source in read_css_sources(RIDE, "ride")


@pytest.mark.parametrize("hook", HOOKS)
def test_every_account_page_hook_is_styled(source: str, hook: str) -> None:
    assert hook in source


def test_rules_sit_in_the_overrides_layer(source: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S).strip()
    assert code.startswith("@layer overrides {")


def test_colours_only_through_tokens(source: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    assert RAW_COLOUR.findall(code) == []


def test_no_external_request_and_no_important(source: str) -> None:
    assert "@import" not in source
    assert "!important" not in source
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", source)


def test_the_file_is_small(source: str) -> None:
    assert len(source.encode()) < 12_000
