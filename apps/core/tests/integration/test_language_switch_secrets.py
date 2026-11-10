"""I18N.11: a page whose URL carries a secret never writes that URL into its language switcher
or `hreflang` links (crawlers, Referer): its switch goes to the other language's home, no notice."""

import re
import secrets
from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

pytestmark = pytest.mark.django_db


def _urls(key: str) -> list[str]:
    return [
        reverse("vendors:apply_cancel", args=[key]),
        reverse("vendors:apply_confirm", args=[key]),
        reverse("vendor_claim:claim", args=[key]),
        reverse("action_link:open", args=[key]),
    ]


@pytest.mark.parametrize("which", range(4))
def test_a_secret_url_is_never_echoed_into_the_switcher(client: Client, which: int) -> None:
    key = secrets.token_hex(16)
    response = client.get(_urls(key)[which])
    html = response.content.decode()
    for value in re.findall(r'name="next" value="([^"]*)"', html):
        assert key not in value
        assert value in ("/", "/it/", "/de/", "/fr/", "/es/"), value
    assert 'rel="alternate"' not in html
    assert 'name="notice" value="1"' not in html


SECRET_KWARG = re.compile(r"token|key|uid|code|secret|signature|hash", re.IGNORECASE)

# Patterns with a secret kwarg that are safe without `language_switch_home`, and why.
ALLOWED = {
    # Wagtail's own admin password reset: its pages use the admin layout, which has no
    # language switcher and no hreflang, so the URL is never written into the HTML.
    "admin/password_reset/confirm/<uidb64>/<token>/": "Wagtail admin layout, no switcher",
}


def _patterns(patterns: Iterable[Any], prefix: str = "") -> Iterator[tuple[str, URLPattern]]:
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _patterns(pattern.url_patterns, route)
        else:
            yield route, pattern


def test_every_view_with_a_secret_in_its_url_opts_out_of_the_language_switch() -> None:
    """A future secret-bearing view (token, key, uid, code...) cannot forget the opt-out: the
    walk covers every pattern, including the language-prefixed ones and third-party apps."""
    missing = []
    seen = 0
    for route, pattern in _patterns(get_resolver().url_patterns):
        names = [a or b for a, b in re.findall(r"\(\?P<(\w+)>|<(?:\w+:)?(\w+)>", route)]
        if not any(SECRET_KWARG.search(name) for name in names):
            continue
        seen += 1
        if route in ALLOWED:
            continue
        if not getattr(pattern.callback, "language_switch_home", False):
            missing.append(route)
    assert seen >= 5  # the walk really finds the known secret URLs
    assert missing == [], f"add @language_switch_home (or an ALLOWED reason): {missing}"
