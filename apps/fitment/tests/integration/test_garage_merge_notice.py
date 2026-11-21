"""FIT.7 (2/n): the sign-in merge notice and its two posts (spec 17 §17.4.3, FIT.6 case 8)."""

import secrets

import pytest
from django.db import connection
from django.template import Context
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from apps.accounts.tests.factories import UserFactory
from apps.core.i18n import primary_language
from apps.fitment import garage, garage_merge
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory

pytestmark = pytest.mark.django_db

MERGE = "garage_merge"


def _sentence(count: int) -> str:
    """The notice's sentence in the primary language (CI also runs with `it,de`)."""
    singular = "You have %(counter)s profile saved on this device. Add it to your account?"
    plural = "You have %(counter)s profiles saved on this device. Add them to your account?"
    with translation.override(primary_language()):
        return translation.ngettext(singular, plural, count) % {"counter": count}


@pytest.fixture(autouse=True)
def catalogue() -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    for name in ("Roadster", "Tourer"):
        FitmentNodeFactory.create(name=name, parent=make)


def _cookie(*models: str) -> str:
    vehicles = tuple(garage.Vehicle("Acme", m, "") for m in models)
    return garage.dumps(garage.Garage(vehicles, 0))


def _sign_in(client: Client, user: object, *models: str) -> None:
    """Sign in with the visitor's cookie in the browser, the way the real form does."""
    client.cookies[garage.COOKIE_NAME] = _cookie(*models)
    client.force_login(user)  # type: ignore[arg-type]
    session = client.session
    session.pop(garage_merge.SESSION_KEY, None)
    session.save()
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.session = SessionStore(session_key=session.session_key)
    request.COOKIES = {garage.COOKIE_NAME: _cookie(*models)}
    garage_merge.stage_on_login(type(user), request, user)  # type: ignore[arg-type]
    request.session.save()


def _page(client: Client) -> str:
    return client.get("/").content.decode()


def test_the_notice_shows_after_sign_in_and_not_for_a_visitor(client: Client) -> None:
    assert "data-garage-merge-notice" not in _page(client)
    user = UserFactory.create()
    _sign_in(client, user, "Roadster", "Tourer")
    html = _page(client)
    assert _sentence(2) in html
    assert reverse(MERGE) in html
    assert 'value="add"' in html
    assert 'value="dismiss"' in html
    assert 'role="status"' in html
    assert "<h2" not in html[html.index("data-garage-merge-notice") :].split("</section>")[0]
    assert 'name="next"' not in html.split("data-garage-merge-notice")[1].split("</section>")[0]


def test_not_now_merges_nothing_and_keeps_the_cookie(client: Client) -> None:
    user = UserFactory.create()
    _sign_in(client, user, "Roadster")
    response = client.post(reverse(MERGE), {"action": "dismiss"}, headers={"Referer": "/shop/"})
    assert response.status_code == 302
    assert response["Location"] == "/shop/"
    assert not GarageProfile.objects.filter(user=user).exists()
    assert garage.COOKIE_NAME not in response.cookies  # untouched
    assert "data-garage-merge-notice" not in _page(client)  # asked once


def test_add_merges_and_clears_the_cookie(client: Client) -> None:
    user = UserFactory.create()
    _sign_in(client, user, "Roadster", "Tourer")
    response = client.post(reverse(MERGE), {"action": "add"}, headers={"Referer": "/shop/"})
    assert response["Location"] == "/shop/"
    assert GarageProfile.objects.filter(user=user).count() == 2
    assert response.cookies[garage.COOKIE_NAME].value == ""  # cleared
    assert "data-garage-merge-notice" not in _page(client)


def test_a_shared_computer_never_merges_without_add(client: Client) -> None:
    visitor_a = _cookie("Roadster", "Tourer")
    user_b = UserFactory.create()
    client.cookies[garage.COOKIE_NAME] = visitor_a
    client.force_login(user_b)
    client.get("/")
    client.get("/")
    assert not GarageProfile.objects.filter(user=user_b).exists()


def test_the_posts_are_explicit_and_safe(client: Client) -> None:
    assert client.post(reverse(MERGE), {"action": "add"}).status_code == 302  # to sign in
    user = UserFactory.create()
    _sign_in(client, user, "Roadster")
    assert client.get(reverse(MERGE)).status_code == 405
    assert client.post(reverse(MERGE), {"action": "nope"}).status_code == 400
    response = client.post(
        reverse(MERGE), {"action": "dismiss"}, headers={"Referer": "https://evil.example/"}
    )
    assert response["Location"] == "/"  # a foreign Referer is dropped
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert strict.post(reverse(MERGE), {"action": "add"}).status_code == 403


def test_the_notice_costs_no_query_for_a_visitor_and_one_session_read_signed_in(
    client: Client,
) -> None:
    from apps.fitment.templatetags.garage_tags import garage_merge_notice

    request = client.get("/").wsgi_request
    with CaptureQueriesContext(connection) as queries:
        garage_merge_notice(Context({"request": request}))
    assert len(queries) == 0
    user = UserFactory.create()
    _sign_in(client, user, "Roadster")
    request = client.get("/").wsgi_request
    with CaptureQueriesContext(connection) as queries:
        garage_merge_notice(Context({"request": request}))
        garage_merge_notice(Context({"request": request}))
    assert len(queries) <= 1


def test_a_double_add_adds_once(client: Client) -> None:
    user = UserFactory.create()
    _sign_in(client, user, "Roadster", "Tourer")
    client.post(reverse(MERGE), {"action": "add"})
    client.cookies[garage.COOKIE_NAME] = _cookie("Roadster", "Tourer")  # a replay with the cookie
    client.post(reverse(MERGE), {"action": "add"})
    assert GarageProfile.objects.filter(user=user).count() == 2


def test_add_after_not_now_merges_nothing(client: Client) -> None:
    user = UserFactory.create()
    _sign_in(client, user, "Roadster")
    client.post(reverse(MERGE), {"action": "dismiss"})
    client.post(reverse(MERGE), {"action": "add"})
    assert not GarageProfile.objects.filter(user=user).exists()


def test_the_notice_never_writes_the_page_url_and_skips_error_pages(client: Client) -> None:
    user = UserFactory.create()
    _sign_in(client, user, "Roadster")
    secret = "tok-" + secrets.token_urlsafe(12)
    html = client.get(f"/missing/{secret}/").content.decode()  # an error page
    assert "data-garage-merge-notice" not in html
    assert client.get("/", {"t": secret}).content.decode().count(secret) == 0
    from apps.fitment.templatetags.garage_tags import garage_merge_notice

    request = client.get("/").wsgi_request
    assert garage_merge_notice(Context({"request": request, "exception": Exception()})) == {}
