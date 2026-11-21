"""FIT.7 (1/n): the header garage menu and the chooser under the ride theme (a cross-app test:
fitment never imports the themes app)."""

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation

from apps.accounts.tests.factories import UserFactory
from apps.core.i18n import primary_language
from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db


def test_the_ride_header_carries_the_garage_menu_and_the_chooser_renders() -> None:
    request = RequestFactory().get("/")
    request.user = UserFactory.create()
    with use_theme("ride"):
        header = render_to_string(
            "components/header.html", {"site_name": "Shop", "request": request}, request=request
        )
        chooser = render_to_string(
            "fitment/garage_choose.html",
            {"noun": "Garage", "options": [], "active_label": "", "next": "/"},
            request=request,
        )
    assert "data-garage-menu" in header
    with translation.override(primary_language()):
        assert translation.gettext("Add your garage") in header
    assert reverse("garage_choose") in header
    assert "data-garage-empty" in chooser
