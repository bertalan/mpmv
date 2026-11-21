"""FIT.5 (2/3): the "My garage" page under the ride theme (a cross-app test: fitment never imports
the themes app)."""

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.fitment import garage_views
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)
from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db


def test_the_ride_theme_renders_the_page() -> None:
    for position, code in enumerate(["make", "model"], start=1):
        FitmentLevelFactory.create(position=position, code=code)
    user = UserFactory.create()
    node = FitmentNodeFactory.create(parent=FitmentNodeFactory.create(name="Make One"))
    GarageProfileFactory.create(user=user, name="Ride bike", node=node, is_active=True)
    request = RequestFactory().get(reverse("garage_account"))
    request.user = user
    with use_theme("ride"):
        html = render_to_string(
            "fitment/garage_account.html", garage_views.page_context(user), request=request
        )
    assert "Ride bike" in html
    assert "data-garage-active" in html
    assert "list-group-item" in html  # the ride override, not the base list
    assert "card" in html  # the ride account layout
