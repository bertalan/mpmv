"""ARC.37: the footer help link of both themes opens the customer help centre (`/help-centre/`)."""

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.themes.context_processors import theme as theme_context
from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db


def test_the_footer_of_both_themes_links_the_help_centre(settings: object) -> None:
    settings.MPMV_HELP_URL = ""  # type: ignore[attr-defined]
    request = RequestFactory().get("/")
    ctx = theme_context(request)
    assert ctx["help_url"] == "/help-centre/"
    for slug in ("base", "ride"):
        with use_theme(slug):
            html = render_to_string("components/footer.html", {"site_name": "X", **ctx})
        assert '<a class="help-link" href="/help-centre/">' in html, slug


def test_a_configured_help_address_wins(settings: object) -> None:
    settings.MPMV_HELP_URL = "/about/#help"  # type: ignore[attr-defined]
    assert theme_context(RequestFactory().get("/"))["help_url"] == "/about/#help"
