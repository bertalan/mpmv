"""A second consent provider for tests (SEO.9): proves the tag-gating code does not depend on the
self-hosted one. It keeps its choice in a header-less in-memory way: allowed only after `grant`."""

from collections.abc import Iterable

from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.seo import engine
from apps.seo.providers import ConsentProvider


class FakeConsentProvider(ConsentProvider):
    name = "fake"
    granted: set[str] = set()
    recorded: list[tuple[str, tuple[str, ...]]] = []

    def state(self, request: HttpRequest) -> engine.ConsentState:
        if not self.granted:
            return engine.UNDECIDED
        return engine.ConsentState(True, frozenset(self.granted), "fake", "0" * 32)

    def render_banner(self, request: HttpRequest | None) -> str:
        return mark_safe('<div id="fake-cmp-banner" hidden></div>')

    def gate_script(self, category: str, src: str) -> str:
        return format_html(
            '<script type="text/plain" data-cmp-category="{}" data-cmp-src="{}"></script>',
            category,
            src,
        )

    def record(
        self, request: HttpRequest, action: str, selected: Iterable[str] = ()
    ) -> engine.ConsentState:
        self.recorded.append((action, tuple(selected)))
        return self.state(request)

    def csp_origins(self) -> tuple[str, ...]:
        return ("https://cmp.example.test",)
