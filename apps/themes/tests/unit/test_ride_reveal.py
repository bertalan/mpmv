"""THM.14 part 2: `ride` reveal, counters, progress, section title, countdown and video modal.

Spec 14 §14.3: content is visible without script and under reduced motion; the counter's final
number is in the HTML; the video is a link first and an embed only after consent. The browser
checks are in tests/e2e/test_ride_reveal.py.
"""

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from django.template.loader import render_to_string

from apps.themes.build import read_css_sources
from apps.themes.services import use_theme
from apps.themes.tests.builders import SHIPPED_THEMES

RIDE = SHIPPED_THEMES / "ride"
JS = RIDE / "static/themes/ride/js"
pytestmark = pytest.mark.usefixtures("english_interface")
FORBIDDEN = ("slick", "owl", "swiper", "wow.js", "animate.css", "jquery")


def render(name: str, /, **context: Any) -> str:
    with use_theme("ride"):
        return render_to_string(f"components/{name}.html", context).strip()


@pytest.fixture(scope="module")
def css() -> str:
    return "\n".join(read_css_sources(RIDE, "ride"))


class TestCounter:
    html = render("counter", value=1250, label="Orders shipped", suffix="+")

    def test_the_final_number_is_in_the_html_and_screen_readers_get_it_only(self) -> None:
        assert 'data-counter="1250"' in self.html
        assert 'aria-hidden="true"' in self.html  # the animated digits
        # The final value for readers, with the active language's thousands separator.
        assert re.search(r'visually-hidden">1[,.\u00a0\u202f\u2019 ]?250\+', self.html)
        assert "Orders shipped" in self.html

    def test_has_no_inline_style(self) -> None:
        assert "style=" not in self.html


class TestProgress:
    html = render("progress", id="p1", label="Order packed", value=60, max=100)

    def test_is_a_native_progress_with_a_label_and_the_value_as_text(self) -> None:
        assert re.search(r'<label[^>]*for="p1"[^>]*>Order packed</label>', self.html)
        assert re.search(r'<progress[^>]*id="p1"[^>]*value="60"[^>]*max="100"', self.html)
        assert "60%" in self.html

    def test_max_defaults_to_one_hundred(self) -> None:
        assert 'max="100"' in render("progress", id="p2", label="x", value=5)


class TestSectionTitle:
    def test_has_an_overline_and_a_heading_of_the_given_level(self) -> None:
        html = render("section_title", overline="Our range", title="Gear", heading_level=3)
        assert "Our range" in html
        assert re.search(r"<h3[^>]*>Gear</h3>", html)

    def test_the_overline_is_optional(self) -> None:
        html = render("section_title", title="Gear")
        assert "overline" not in html
        assert "<h2" in html


class TestLaunchCountdown:
    target = datetime(2031, 1, 1, 9, 0, tzinfo=UTC)

    def test_ride_overrides_it_and_keeps_the_hooks_and_the_server_rendered_date(self) -> None:
        html = render("launch_countdown", title="New jacket", target=self.target, heading_level=3)
        for hook in (
            "data-countdown",
            'data-target="2031-01-01T09:00:00Z"',
            "data-countdown-upcoming",
            "data-countdown-counter",
            "data-countdown-remaining",
            "data-countdown-pause",
            "data-countdown-expired",
            'data-resume-label="Resume countdown"',
        ):
            assert hook in html, hook
        assert "ride-countdown" in html
        assert "<time" in html
        assert "Available from" in html
        assert "aria-live" not in html  # the counter is never a live region

    def test_expired_state_is_server_rendered(self) -> None:
        html = render(
            "launch_countdown",
            title="Rain cover",
            target=self.target,
            expired=True,
            expired_text="Now.",
        )
        assert "launch-countdown--expired" in html
        assert "Now." in html
        assert "data-target" not in html


class TestVideoModal:
    html = render(
        "video_modal",
        id="v1",
        title="Workshop tour",
        url="https://video.example/watch/1",
        embed_url="https://video.example/embed/1",
    )

    def test_is_a_plain_link_first(self) -> None:
        assert re.search(r'<a[^>]*href="https://video.example/watch/1"', self.html)
        assert "<iframe" not in self.html  # no third-party request before consent

    def test_opens_a_bootstrap_modal_and_is_consent_gated(self) -> None:
        assert 'data-bs-toggle="modal"' in self.html
        assert 'data-bs-target="#v1-modal"' in self.html
        assert 'class="modal' in self.html
        assert 'aria-labelledby="v1-modal-title"' in self.html
        assert 'data-consent="marketing"' in self.html  # makes the page load the consent module
        assert 'data-embed="https://video.example/embed/1"' in self.html

    def test_the_placeholder_points_to_the_cookie_settings(self) -> None:
        assert "data-video-gate" in self.html
        assert "/cookie-settings/" in self.html

    def test_has_no_inline_style_or_handler(self) -> None:
        assert "style=" not in self.html
        assert not re.search(r"\son[a-z]+=", self.html)

    def test_an_unsafe_embed_url_is_dropped(self) -> None:
        html = render("video_modal", id="v2", title="T", url="/v/", embed_url="javascript:alert(1)")
        assert "javascript:" not in html


class TestScriptsAndStyles:
    def test_reveal_script_is_small_and_guarded(self) -> None:
        script = (JS / "reveal.js").read_text(encoding="utf-8")
        assert "IntersectionObserver" in script
        assert "prefers-reduced-motion" in script
        assert "is-visible" in script
        assert "eval(" not in script

    def test_video_script_creates_the_frame_only_after_consent(self) -> None:
        script = (JS / "video.js").read_text(encoding="utf-8")
        assert "mpmvConsent" in script
        assert "show.bs.modal" in script
        assert "eval(" not in script

    def test_the_layout_loads_both_modules(self) -> None:
        layout = (RIDE / "templates/layouts/base.html").read_text(encoding="utf-8")
        assert "themes/ride/js/reveal.js" in layout
        assert "themes/ride/js/video.js" in layout

    def test_reveal_hides_only_with_javascript_and_only_without_reduced_motion(
        self, css: str
    ) -> None:
        assert re.search(
            r"@media \(prefers-reduced-motion: no-preference\)\s*\{[^@]*?data-reveal", css
        ), "reveal styles must sit inside no-preference"
        assert re.search(r"html:not\(\.no-js\)[^{]*\[data-reveal\]", css)

    def test_no_forbidden_library(self) -> None:
        for path in (RIDE / "static").rglob("*"):
            if path.is_file() and "vendor" not in path.parts and path.suffix in {".js", ".css"}:
                text = path.read_text(encoding="utf-8").lower()
                assert not any(word in text for word in FORBIDDEN), path
