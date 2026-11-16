"""The ride override of the passkey prompt (THM.16 2/2, #530 review): it must follow the base page
(D-32, CORE.25) with Bootstrap classes only: the current URL names, the shop-window paragraph and
the translated wording. A stale copy reversed a removed URL and gave a 500 on every refusal."""

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db

TEMPLATE = "accounts/levels/prompt.html"


def _render(**context: Any) -> str:
    request = RequestFactory().get("/manager/shop/sales-terms/")
    request.user = AnonymousUser()
    base = {
        "action": "sales_terms_publish",
        "group": 2,
        "digest": "0" * 64,
        "can_elevate": True,
        "can_do_once": True,
        "shop_window": False,
        "has_passkey": True,
        "replay_method": "POST",
        "replay_url": "/manager/shop/sales-terms/",
        "replay_fields": [("body", "x")],
    }
    with use_theme("ride"):
        return render_to_string(TEMPLATE, base | context, request=request)


def test_the_ride_prompt_renders_with_the_current_url_names() -> None:
    html = _render()
    for name in ("levels:options", "levels:window", "levels:once_options", "levels:once"):
        assert reverse(name) in html, name
    assert "btn btn-primary" in html
    assert "Operational mode" not in html
    assert "Secure mode" not in html


def test_the_ride_prompt_shows_the_shop_window_note_only_for_a_shop_window() -> None:
    note = "It never covers another shop."
    assert note in _render(shop_window=True)
    assert note not in _render(shop_window=False)


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"group": 3}, "asks for a passkey of its own each time"),
        ({"can_do_once": False}, "Open passkey window"),
        ({"can_elevate": False, "has_passkey": False}, "Add a passkey"),
        ({"can_elevate": False, "has_passkey": True}, "Ask a platform owner"),
    ],
)
def test_the_ride_prompt_keeps_every_variant_of_the_base_page(
    context: dict[str, Any], expected: str
) -> None:
    assert expected in _render(**context)


def test_the_ride_prompt_is_translated() -> None:
    from django.utils import translation

    with translation.override("it"):
        italian = _render()
    with translation.override("en"):
        english = _render()
    assert "This needs your passkey" in english
    assert italian != english  # the Italian catalogue covers the refusal page (6fca0f69)
