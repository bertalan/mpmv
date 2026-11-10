"""I18N.11: a page whose URL carries a secret never writes that URL into its language switcher
or `hreflang` links (crawlers, Referer): its switch goes to the other language's home, no notice."""

import re
import secrets

import pytest
from django.test import Client
from django.urls import reverse

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
