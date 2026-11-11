"""I18N.7: the Manager renders in Italian for an Italian user."""

import pytest
from django.test import Client

from apps.accounts.tests.mfa import verified_login
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db


def test_the_manager_shell_and_dashboard_are_in_italian(client: Client) -> None:
    member = VendorMemberFactory.create(role="owner").user
    member.preferred_language = "it"
    member.save()
    verified_login(client, member)
    html = client.get("/manager/shop/vendor/", headers={"accept-language": "en"}).text
    assert "Esci" in html
    assert "Sezioni del Manager" in html
    assert "Sign out" not in html
