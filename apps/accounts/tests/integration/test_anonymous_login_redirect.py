"""Anonymous visitors of signed-in pages are sent to the site login, never to /django-admin/.

The django-admin login is loopback-only (OPS.5), so a redirect there is a 404 from outside.
"""

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

SIGNED_IN_PAGES = [
    "/account/mfa/",
    "/account/mfa/setup/",
    "/account/privacy/",
    "/account/level/options/",
    "/account/level/operational/",
    "/_styleguide/",
]


@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_an_anonymous_visitor_is_sent_to_the_site_login(client: Client, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 302
    assert response["Location"].startswith("/account/login/?next=")
    assert "django-admin" not in response["Location"].split("?next=")[0]
