"""THM.27 (2/2): the `ride` sign-in keeps the account layout's Manager entry."""

import re

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings

from apps.accounts.tests.factories import UserFactory
from apps.manager.spaces import Space
from apps.manager.templatetags import manager_tags

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]


def test_the_sign_in_keeps_the_manager_entry_of_the_account_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager_tags, "may_enter", lambda space, user: space is Space.PLATFORM)
    request = RequestFactory().get("/")
    request.user = UserFactory.create(superuser=True)
    with override_settings(MPMV_THEME_DEFAULT="ride"):
        html = render_to_string("manager/login.html", {"form": None, "next": ""}, request=request)
    assert re.search(r'<p class="fw-semibold"><a href="[^"]+">Manager</a></p>', html)
    assert "signin-control" in html
