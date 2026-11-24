"""THM.35: the `ride` compact language dropdown (spec 14 §14.6).

Rendered with the `ride` theme active and no request. The keyboard behaviour, axe and the visual
baselines are in tests/e2e/test_ride_language_menu.py.
"""

import re
import tomllib
from typing import Any

import pytest
from django.template.loader import render_to_string

from apps.themes.engine import LIGHT_SURFACE, UI_CONTRAST, contrast_ratio
from apps.themes.flags import LANGUAGE_FLAGS
from apps.themes.palettes import PRESETS
from apps.themes.services import use_theme
from apps.themes.tests.builders import SHIPPED_THEMES

pytestmark = pytest.mark.usefixtures("english_interface")

RIDE = SHIPPED_THEMES / "ride"
FLAGS = RIDE / "static" / "themes" / "ride" / "flags"
LANGUAGES = [
    {"code": "en", "name_local": "English"},
    {"code": "de", "name_local": "Deutsch"},
    {"code": "it", "name_local": "Italiano"},
    {"code": "fr", "name_local": "Français"},
]


def render(languages: list[dict[str, Any]] = LANGUAGES, current: str = "en") -> str:
    with use_theme("ride"):
        return render_to_string(
            "components/language_switcher.html",
            {"languages": languages, "current_language": current, "next_url": "/x/"},
        ).strip()


def test_the_toggle_is_one_disclosure_button_named_language_and_the_current_endonym() -> None:
    html = render()
    pattern = r'<button type="button"[^>]*language-menu__toggle[^>]*>.*?</button>'
    toggle = re.search(pattern, html, re.S)
    assert toggle, html
    text = toggle.group(0)
    assert 'aria-expanded="false"' in text
    assert 'aria-controls="language-menu-list"' in text
    assert 'role="menu' not in html
    # The accessible name: the hidden "Language: " plus the endonym; the icons add nothing.
    plain = re.sub(r"<svg.*?</svg>", "", text)
    assert re.sub(r"<[^>]+>", "", plain).split() == ["Language:", "English"]
    assert text.count('aria-hidden="true"') == 2


def test_every_item_is_its_own_post_form_with_endonym_lang_and_a_decorative_flag() -> None:
    html = render()
    assert html.count("<form") == 4
    assert html.count('method="post"') == 4
    names = (("en", "English"), ("de", "Deutsch"), ("it", "Italiano"), ("fr", "Français"))
    for code, label in names:
        pattern = rf'<button type="submit"[^>]*value="{code}"[^>]*>(.*?)</button>'
        item = re.search(pattern, html, re.S)
        assert item, code
        assert f'lang="{code}"' in item.group(0)
        assert item.group(1).endswith(label)
        flag = re.search(r"<img[^>]*>", item.group(1))
        assert flag
        assert 'alt=""' in flag.group(0)
        assert 'aria-hidden="true"' in flag.group(0)
    assert 'name="next" value="/x/"' in html
    current = re.search(r'<button type="submit"[^>]*value="en"[^>]*>', html)
    assert current
    assert 'aria-current="true"' in current.group(0)
    assert "disabled" in current.group(0)


@pytest.mark.parametrize(
    ("code", "flag"), [("en", "gb"), ("de", "de"), ("it", "it"), ("fr", "fr"), ("es", "es")]
)
def test_the_default_country_of_each_language_has_its_flag(code: str, flag: str) -> None:
    html = render([{"code": code, "name_local": code}], current=code)
    assert f"/flags/{flag}.svg" in html


def test_every_mapped_flag_exists_is_own_origin_and_has_no_coat_of_arms() -> None:
    for code, stem in LANGUAGE_FLAGS.items():
        svg = (FLAGS / f"{stem}.svg").read_text()
        assert svg.startswith("<svg"), code
        assert "<image" not in svg
        assert "href" not in svg
        assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
        assert len(svg) < 1024
    assert set(LANGUAGE_FLAGS) >= {"en", "de", "it", "fr", "es"}
    assert LANGUAGE_FLAGS["en"] == "gb"


def test_no_emoji_flag_and_no_request_leaves_the_origin() -> None:
    html = render()
    assert not re.search("[\U0001f1e6-\U0001f1ff]", html)
    assert not re.search(r'(?:src|href)="(?:https?:)?//', html)


def test_a_language_without_a_flag_shows_its_endonym_alone() -> None:
    html = render([{"code": "pt", "name_local": "Português"}], current="pt")
    assert "<img" not in html.split("</button>", 1)[1]
    assert "Português" in html


def test_language_codes_stay_as_configured() -> None:
    html = render()
    assert "en-gb" not in html
    assert "de-at" not in html


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_the_flag_outline_is_3_to_1_on_the_band_and_the_surface_of_each_palette(slug: str) -> None:
    palette = PRESETS[slug]
    for background in (palette["band"], LIGHT_SURFACE, palette["primary_tint"]):
        assert contrast_ratio(palette["control_border"], background) >= UI_CONTRAST, background


def test_the_flags_are_recorded_in_licenses_toml() -> None:
    listed = [
        f
        for asset in tomllib.loads((RIDE / "LICENSES.toml").read_text())["asset"]
        for f in asset["files"]
    ]
    for stem in set(LANGUAGE_FLAGS.values()):
        assert f"flags/{stem}.svg" in listed


def test_without_javascript_the_list_is_shown_and_the_script_is_loaded_by_the_layout() -> None:
    css = (RIDE / "static/themes/ride/css/20-header-footer.css").read_text()
    assert "html.no-js .language-menu__list" in css
    assert "html.no-js .language-menu__toggle" in css
    layout = (RIDE / "templates/layouts/base.html").read_text()
    assert "themes/ride/js/language-menu.js" in layout
