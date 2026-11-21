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


def test_the_ride_page_carries_the_merge_notice_after_sign_in() -> None:
    from apps.fitment import garage_merge

    request = RequestFactory().get("/shop/")
    request.user = UserFactory.create()
    request.session = {garage_merge.SESSION_KEY: {"vehicles": [{}, {}], "active": None}}  # type: ignore[assignment]
    with use_theme("ride"):
        html = render_to_string("components/garage_merge_notice.html", {}, request=request)
    assert "data-garage-merge-notice" in html
    singular = "You have %(counter)s profile saved on this device. Add it to your account?"
    plural = "You have %(counter)s profiles saved on this device. Add them to your account?"
    with translation.override(primary_language()):
        assert translation.ngettext(singular, plural, 2) % {"counter": 2} in html
    assert reverse("garage_merge") in html


def test_the_base_theme_renders_the_merge_notice_without_the_page_url() -> None:
    from apps.fitment import garage_merge

    request = RequestFactory().get("/shop/")
    request.user = UserFactory.create()
    request.session = {garage_merge.SESSION_KEY: {"vehicles": [{}], "active": None}}  # type: ignore[assignment]
    with use_theme("base"):
        html = render_to_string("components/garage_merge_notice.html", {}, request=request)
    singular = "You have %(counter)s profile saved on this device. Add it to your account?"
    with translation.override(primary_language()):
        assert translation.gettext(singular) % {"counter": 1} in html
    assert 'role="status"' in html
    assert "/shop/" not in html
