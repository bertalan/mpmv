"""FIT.7 (1/n): the header garage menu and the chooser under the ride theme (a cross-app test:
fitment never imports the themes app)."""

import pytest
from django.contrib.auth.models import AnonymousUser
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


def test_no_template_script_or_e2e_targets_the_old_user_menu_classes_for_the_garage() -> None:
    """The garage menu uses `.garage-menu__*` since the help sweep forbids `user-menu` on public
    pages: nothing may still select it through the user menu's classes."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    menu = (root / "themes/base/templates/components/garage_menu.html").read_text()
    assert "user-menu" not in menu
    suspects = [
        path
        for pattern in ("tests/e2e/**/*.py", "themes/*/static/themes/*/js/*.js")
        for path in root.glob(pattern)
        if "/vendor/" not in str(path)
        and "garage" in (text := path.read_text())
        and "user-menu" in text
    ]
    assert suspects == []


def test_the_ride_listing_and_base_blog_carry_the_garage_banner_without_the_page_url() -> None:
    from django.http import HttpResponse

    from apps.fitment import garage
    from apps.fitment.tests.factories import FitmentNodeFactory
    from apps.fitment.views import _write

    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    request = RequestFactory().get("/shop/?token=top-secret-marker")
    request.user = AnonymousUser()
    cookie = (
        _write(HttpResponse(), garage.Garage((garage.Vehicle("Acme", "Roadster"),), 0))
        .cookies[garage.COOKIE_NAME]
        .value
    )
    request.COOKIES[garage.COOKIE_NAME] = cookie
    for theme in ("ride", "base"):
        with use_theme(theme):
            html = render_to_string("components/garage_banner.html", {}, request=request)
        assert "data-garage-banner" in html
        assert 'role="status"' in html
        assert reverse("garage_choose") in html
        assert "top-secret-marker" not in html
        assert "/shop/" not in html
        with translation.override(primary_language()):
            assert (
                translation.gettext("Showing items for: %(label)s") % {"label": "Acme, Roadster"}
                in html
            )
