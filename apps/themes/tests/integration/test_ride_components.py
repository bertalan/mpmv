"""THM.9 part 2: the `ride` carousels, the consent banner and the video modal (spec 14 §14.6).

Render tests under `ride`: the strip (testimonials, blog cards) keeps every slide in the markup
and the controls `hidden` until the script runs (the no-JS fallback), the banner is a Bootstrap
alert with the same hooks and form as `base`, and the video modal puts no `<iframe>` in the page.
"""

import re

import pytest
from django.template.loader import render_to_string

from apps.themes.services import use_theme

pytestmark = [pytest.mark.usefixtures("english_interface"), pytest.mark.django_db]

SLIDES = [
    {"title": f"Rider {n}", "text": f"Quote {n}", "url": f"/blog/post-{n}/"} for n in range(1, 6)
]


def banner(theme: str) -> str:
    with use_theme(theme):
        return render_to_string(
            "seo/consent/banner.html",
            {
                "categories": [],
                "action_url": "/consent/",
                "preferences_url": "/cookies/",
                "banner_version": "1",
                "cookie_name": "mpmv_consent",
                "signal_map": "{}",
            },
        )


def test_the_strip_keeps_every_slide_and_hides_the_controls_until_the_script_runs() -> None:
    with use_theme("ride"):
        html = render_to_string(
            "components/strip.html",
            {"id": "voices", "label": "Voices", "slides": SLIDES, "per_view": 2},
        )
    assert 'aria-roledescription="carousel"' in html
    assert html.count('aria-roledescription="slide"') == 3
    for n in range(1, 6):
        assert f"Quote {n}" in html
    controls = re.findall(r"<button[^>]*(?:data-bs-slide|data-ride-toggle)[^>]*>", html)
    assert controls
    assert all(" hidden" in control for control in controls)
    assert "data-autoplay" not in html  # nothing moves unless the editor enables it
    assert 'tabindex="0"' in html  # the group scrolls with the keyboard on a phone


def test_the_banner_is_a_bootstrap_alert_with_the_hooks_of_base() -> None:
    ride, base = banner("ride"), banner("base")
    assert re.search(r'<section class="[^"]*\balert\b[^"]*"', ride)
    assert " hidden>" in ride  # hidden until consent.js finds no valid choice
    for hook in (
        'id="consent-banner"',
        "data-consent-banner",
        "data-consent-form",
        'aria-labelledby="consent-banner-title"',
        "data-script=",
        "data-version=",
        "data-cookie=",
        "data-signals=",
        "data-saved=",
        'method="post"',
    ):
        assert hook in ride
        assert hook in base
    assert 'role="dialog"' not in ride  # not a modal: it never traps the focus


def test_reject_and_accept_weigh_the_same_in_the_ride_banner() -> None:
    html = banner("ride")
    buttons = {
        value: cls
        for cls, value in re.findall(
            r'<button type="submit" class="([^"]*)" name="action" value="(\w+)"', html
        )
    }
    assert buttons["reject_all"] == buttons["accept_all"]
    assert "custom" in buttons
    assert "Reject all" in html
    assert "Accept all" in html


def test_the_video_modal_renders_no_iframe_and_a_gate_with_a_plain_link() -> None:
    with use_theme("ride"):
        html = render_to_string(
            "components/video_modal.html",
            {
                "id": "tour",
                "title": "Tour",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
                "embed_url": "https://www.youtube-nocookie.com/embed/abcdefghijk",
            },
        )
    assert "<iframe" not in html  # the script injects it only after consent
    assert 'data-consent="marketing"' in html
    assert 'href="https://www.youtube.com/watch?v=abcdefghijk"' in html  # works without JS
    assert "data-video-gate" in html
    assert 'class="modal fade"' in html
