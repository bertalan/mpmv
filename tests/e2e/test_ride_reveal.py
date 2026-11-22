"""E2E (THM.14 part 2): the `ride` reveal, counters, progress, countdown and video modal.

Reduced motion shows everything at once and animates nothing; without JavaScript every revealed
element and counter value is readable; the video is a link until the visitor has consented and
makes no third-party request before; axe `wcag22aa` and zero CSP violations at 360, 768 and
1280 px (spec 14 §14.3, §14.6, §14.9).
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.template.defaultfilters import floatformat
from django.utils import translation
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.core.i18n import configured_languages, primary_language
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.test_ride_bootstrap_components import open_ride
from tests.e2e.test_styleguide import open_styleguide

pytestmark = pytest.mark.django_db(transaction=True)


def grouped(number: int) -> str:
    """`number` with the primary language's thousands separator, as the counter shows it."""
    with translation.override(primary_language()):
        return str(floatformat(number, "0g"))


REVEAL = "#sg-reveal [data-reveal]"
COUNTER = "#sg-counters [data-counter]"
VIDEO = "#sg-video"


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_axe_and_csp_are_clean_with_the_motion_components(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings, width)
    for selector in ("#sg-reveal", "#sg-counters", "#sg-progress", "#sg-countdown", VIDEO):
        expect(page.locator(selector)).to_be_attached()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_reduced_motion_shows_every_revealed_element_and_animates_nothing(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)  # reduced motion on
    items = page.locator(REVEAL)
    assert items.count() >= 3
    for index in range(items.count()):
        state = items.nth(index).evaluate(
            "(e) => { const s = getComputedStyle(e);"
            " return [s.opacity, s.transitionDuration, s.transform]; }"
        )
        assert state[0] == "1"
        assert state[1] in {"0s", ""}
        assert state[2] == "none"
    counter = page.locator(COUNTER).first
    page.wait_for_timeout(600)
    expect(counter.locator("[data-counter-text]")).to_have_text(grouped(1250))


def test_with_motion_an_element_is_revealed_once_it_scrolls_into_view_and_counts_up(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 700})
    open_styleguide(page, live_server)
    last = page.locator(REVEAL).last
    assert last.evaluate("(e) => getComputedStyle(e).opacity") == "0"
    last.scroll_into_view_if_needed()
    expect(last).to_have_class(re.compile(r"is-visible"))
    expect(last).to_have_css("opacity", "1")
    counter = page.locator(COUNTER).first
    counter.scroll_into_view_if_needed()
    # The count-up ends on the server-rendered text, not on the browser's Intl format.
    expect(counter.locator("[data-counter-text]")).to_have_text(grouped(1250), timeout=4000)
    assert csp_violations(page) == []


def test_without_javascript_every_revealed_element_and_counter_is_readable(
    browser: Browser, live_server: LiveServer, settings: SettingsWrapper, page: Page
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    open_styleguide(page, live_server)
    state = page.context.storage_state()
    context = browser.new_context(
        java_script_enabled=False, storage_state=state, reduced_motion="no-preference"
    )
    bare = context.new_page()
    bare.goto(f"{live_server.url}/_styleguide/")
    items = bare.locator(REVEAL)
    for index in range(items.count()):
        expect(items.nth(index)).to_have_css("opacity", "1")
        expect(items.nth(index)).to_be_visible()
    expect(bare.locator(COUNTER).first).to_contain_text(grouped(1250))
    expect(bare.locator("#sg-progress progress").first).to_be_visible()
    expect(bare.locator(f"{VIDEO} a[href]").first).to_be_visible()
    expect(bare.locator("[data-countdown-upcoming]").first).to_be_visible()
    context.close()


def test_the_video_is_a_link_with_no_third_party_request_before_consent(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    foreign: list[str] = []
    page.on(
        "request",
        lambda r: foreign.append(r.url) if not r.url.startswith(live_server.url) else None,
    )
    open_ride(page, live_server, settings)
    opener = page.locator(f"{VIDEO} [data-bs-toggle=modal]")
    opener.focus()
    page.evaluate(
        """() => { window.__shown = false;
            document.querySelector("#sg-video-tour-modal").addEventListener(
                "shown.bs.modal", () => { window.__shown = true; }, {once: true}); }"""
    )
    page.keyboard.press("Enter")
    modal = page.locator("#sg-video-tour-modal")
    expect(modal).to_be_visible()
    page.wait_for_function(
        "() => window.__shown === true"
    )  # Bootstrap ignores a hide during the show transition
    expect(modal.locator("[data-video-gate]")).to_be_visible()
    expect(modal.locator("a[href*='cookie-settings']")).to_be_visible()
    assert modal.locator("iframe").count() == 0
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    expect(modal).not_to_have_class(re.compile(r"\bshow\b"))
    expect(opener).to_be_focused()
    assert [url for url in foreign if "video.example" in url] == []
    violations = run_axe(page)
    assert violations == [], format_violations(violations)


def test_a_revealed_element_at_the_very_end_of_the_page_becomes_visible(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 700})
    open_styleguide(page, live_server)
    expect(page.locator("html")).to_have_class(re.compile(r"reveal-ready"))
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    items = page.locator("[data-reveal]")
    for index in range(items.count()):  # including whatever sits at the very end
        expect(items.nth(index)).to_have_css("opacity", "1")


def test_with_reveal_js_blocked_nothing_is_hidden(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.route("**/js/reveal.js", lambda route: route.abort())
    open_styleguide(page, live_server)
    items = page.locator(REVEAL)
    assert items.count() >= 3
    for index in range(items.count()):
        expect(items.nth(index)).to_have_css("opacity", "1")
    expect(page.locator("html")).not_to_have_class(re.compile(r"reveal-ready"))


def test_a_counter_already_on_screen_shows_its_final_value_at_once(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 5000})
    open_styleguide(page, live_server)
    text = page.locator(COUNTER).first.locator("[data-counter-text]")
    assert text.text_content() == grouped(1250)
    page.wait_for_timeout(300)
    assert text.text_content() == grouped(1250)


def test_the_animated_counter_ends_on_the_server_rendered_text(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    """THM.9: the final value keeps the server's localised format, not the browser's Intl one.

    Runs in a configured language whose server grouping differs from the browser's Intl for the
    same number, found at runtime (Italian and Spanish drop the 4-digit group in Intl, French uses
    another space character), and set as the staff user's preferred language.
    """
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    language = None
    for code in configured_languages():
        with translation.override(code):
            server = str(floatformat(1250, "0g"))
        if server != page.evaluate("(lang) => new Intl.NumberFormat(lang).format(1250)", code):
            language = code
            break
    if language is None:
        pytest.skip("no configured language groups 1250 differently from the browser's Intl")
    # The styleguide has no language prefix: the signed-in user's preference decides (I18N.12).
    get_user_model().objects.filter(email="staff@example.com").update(preferred_language=language)
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", language)
    page.route("**/reveal.js*", lambda route: route.abort())
    page.reload()
    server_text = page.locator(COUNTER).first.locator("[data-counter-text]").text_content()
    with translation.override(language):
        assert server_text == str(floatformat(1250, "0g"))
    page.unroute("**/reveal.js*")
    page.reload()
    counter = page.locator(COUNTER).first
    counter.scroll_into_view_if_needed()
    expect(counter.locator("[data-counter-text]")).to_have_text(server_text, timeout=4000)
    page.wait_for_timeout(1500)
    assert counter.locator("[data-counter-text]").text_content() == server_text


def test_a_browser_without_intl_keeps_the_server_text(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    """THM.14: no `Intl` (or a rejected language tag) skips the count-up; the server text stays."""
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(error.stack or ""))
    page.add_init_script("delete window.Intl;")
    open_styleguide(page, live_server)
    counter = page.locator(COUNTER).first
    counter.scroll_into_view_if_needed()
    text = counter.locator("[data-counter-text]")
    expected = text.text_content()
    assert expected == grouped(1250)
    page.wait_for_timeout(1500)
    assert text.text_content() == expected
    expect(counter).to_have_class(re.compile("is-visible"))
    # The base countdown module has its own Intl use; only the reveal module is in scope here.
    assert [e for e in errors if "reveal.js" in e] == []
