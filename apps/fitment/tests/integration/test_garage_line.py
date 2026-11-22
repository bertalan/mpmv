"""FIT.7 (4/n): the dismissible "Add your garage" line on the lists (spec 17 §17.4.4).

A visitor with no vehicle gets one discreet line, not a pop-up, not a live region. *Dismiss* is a
CSRF-checked post that remembers it in the server-side session and returns to the same list by the
Referer; neither the page URL nor a `next` is ever written into the markup. No line with an active
vehicle, on an error page, for a customer who has profiles, or when the garage is off.
"""

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from apps.accounts.tests.factories import UserFactory
from apps.core.i18n import primary_language
from apps.fitment.tests.factories import FitmentNodeFactory

pytestmark = pytest.mark.django_db

LISTING = "/?s=zzz"
DISMISS = "garage_line_dismiss"


def _t(msgid: str) -> str:
    with translation.override(primary_language()):
        return translation.gettext(msgid)


@pytest.fixture(autouse=True)
def vehicles() -> None:
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)


def _line(html: str) -> str:
    match = re.search(r"<(?:div|p|section)[^>]*data-garage-line.*?</(?:div|p|section)>", html, re.S)
    assert match, "the list carries no Add your garage line"
    return match.group(0)


def _save(client: Client) -> None:
    client.post(
        reverse("garage_save"), {"make": "Acme", "model": "Roadster", "year": "", "next": "/"}
    )


def test_a_visitor_without_a_garage_sees_one_polite_line(client: Client) -> None:
    html = client.get(LISTING).content.decode()
    line = _line(html)
    assert html.count("data-garage-line") == 1
    assert _t("Add your garage") in line
    assert f'href="{reverse("garage_choose")}"' in line
    assert f'action="{reverse(DISMISS)}"' in line
    assert _t("Dismiss") in line
    assert "csrfmiddlewaretoken" in line
    for forbidden in ("role=", "aria-live", "autofocus", "tabindex", "<dialog", "modal"):
        assert forbidden not in line


def test_the_line_never_writes_the_page_url_or_a_next(client: Client) -> None:
    html = client.get("/?s=zzz&token=top-secret-marker").content.decode()
    line = _line(html)
    assert "next" not in line
    assert "top-secret-marker" not in line
    assert "zzz" not in line


def test_no_line_with_an_active_vehicle_or_after_save(client: Client) -> None:
    _save(client)
    assert "data-garage-line" not in client.get(LISTING).content.decode()


def test_the_line_comes_back_when_the_vehicle_is_forgotten(client: Client) -> None:
    _save(client)
    client.post(reverse("garage_forget"), {"next": "/"})
    assert "data-garage-line" in client.get(LISTING).content.decode()


def test_dismissing_hides_the_line_for_the_session_and_returns_by_the_referer(
    client: Client,
) -> None:
    client.get(LISTING)
    response = client.post(reverse(DISMISS), headers={"Referer": "/?s=zzz&page=2"})
    assert response.status_code == 302
    assert response["Location"] == "/?s=zzz&page=2"
    assert "data-garage-line" not in client.get(LISTING).content.decode()
    assert "data-garage-line" not in client.get("/").content.decode()  # reload, other pages


def test_dismissing_sets_no_cookie_of_its_own(client: Client) -> None:
    response = client.post(reverse(DISMISS))
    assert set(response.cookies) <= {"sessionid"}
    assert "mpmv_garage" not in response.cookies


def test_dismiss_is_post_only_and_csrf_checked() -> None:
    strict = Client(enforce_csrf_checks=True)
    assert strict.post(reverse(DISMISS)).status_code == 403
    assert Client().get(reverse(DISMISS)).status_code == 405


def test_dismiss_never_returns_to_a_foreign_or_secret_url(client: Client) -> None:
    foreign = client.post(reverse(DISMISS), headers={"Referer": "https://evil.example/x"})
    assert foreign["Location"] == "/"
    link = reverse("staff_draft_link", kwargs={"token": "top-secret-marker"})
    secret = client.post(reverse(DISMISS), headers={"Referer": link})
    assert "top-secret-marker" not in secret["Location"]
    assert (
        client.post(reverse(DISMISS), headers={"Referer": reverse("garage_choose")})["Location"]
        == "/"
    )
    assert client.post(reverse(DISMISS), {"next": "https://evil.example/"})["Location"] == "/"


def test_no_line_on_an_error_page_and_no_garage_query(client: Client) -> None:
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/no-such-page-here/")
    assert response.status_code == 404
    assert "data-garage-line" not in response.content.decode()
    assert not [q for q in queries.captured_queries if "fitment_" in q["sql"]]


def test_a_customer_with_profiles_sees_no_line_one_without_sees_it(client: Client) -> None:
    from apps.fitment import garage_profiles
    from apps.fitment.tests.factories import GarageProfileFactory

    user = UserFactory.create()
    client.force_login(user)
    assert "data-garage-line" in client.get(LISTING).content.decode()
    GarageProfileFactory.create(user=user)
    assert garage_profiles.profiles_of(user)
    assert "data-garage-line" not in client.get(LISTING).content.decode()


def test_no_line_when_the_garage_is_off(client: Client, settings: object) -> None:
    settings.GARAGE_ANON_STORAGE = "off"  # type: ignore[attr-defined]
    assert "data-garage-line" not in client.get(LISTING).content.decode()


def _with_session(client: Client) -> Client:
    session = client.session  # a session cookie exists, as for any visitor who has used the shop
    session["unrelated"] = 1
    session.save()
    return client


def _queries(client: Client) -> int:
    client.get(LISTING)  # warm caches
    with CaptureQueriesContext(connection) as queries:
        client.get(LISTING)
    return len(queries.captured_queries)


def test_the_line_costs_the_same_queries_as_the_dismissed_page() -> None:
    """The baseline is the same list once dismissed: the line shares the header menu's cached
    state and the one session read, so the number of queries is equal, not just bounded."""
    shown = _with_session(Client())
    assert "data-garage-line" in shown.get(LISTING).content.decode()
    dismissed = _with_session(Client())
    dismissed.post(reverse(DISMISS))
    assert "data-garage-line" not in dismissed.get(LISTING).content.decode()
    assert _queries(shown) == _queries(dismissed)


def test_a_visitor_without_any_cookie_costs_no_session_query() -> None:
    client = Client()
    client.get(LISTING)
    with CaptureQueriesContext(connection) as queries:
        client.get(LISTING)
    assert not [q for q in queries.captured_queries if "django_session" in q["sql"]]


def test_past_the_limit_the_dismissal_is_refused_and_mints_no_session() -> None:
    from django.contrib.sessions.models import Session

    from apps.fitment import views

    for _ in range(views._LINE_LIMIT):
        Client().post(reverse(DISMISS))
    before = Session.objects.count()
    response = Client().post(reverse(DISMISS))
    assert response.status_code == 429
    assert int(response["Retry-After"]) >= 1
    assert "sessionid" not in response.cookies
    assert Session.objects.count() == before
