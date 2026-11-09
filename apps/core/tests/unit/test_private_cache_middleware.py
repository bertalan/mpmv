"""SEC.5: a response for a signed-in user is never stored by a shared cache."""

from types import SimpleNamespace

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.core.middleware import PrivateCacheForSignedInUsersMiddleware


def _run(user: object | None, response: HttpResponse, *, cookie: bool = True) -> HttpResponse:
    request = RequestFactory().get("/")
    if cookie:
        request.COOKIES[settings.SESSION_COOKIE_NAME] = "session-key"
    if user is not None:
        request.user = user  # type: ignore[assignment]

    def view(_: HttpRequest) -> HttpResponse:
        return response

    result = PrivateCacheForSignedInUsersMiddleware(view)(request)
    assert isinstance(result, HttpResponse)
    return result


def _directives(response: HttpResponse) -> set[str]:
    header = response.headers.get("Cache-Control", "")
    return {item.strip() for item in header.split(",") if item.strip()}


def test_a_signed_in_user_gets_a_private_uncacheable_response() -> None:
    response = _run(SimpleNamespace(is_authenticated=True), HttpResponse())
    assert {"private", "no-store", "no-cache"} <= _directives(response)


def test_an_explicit_cache_policy_from_the_view_is_kept() -> None:
    response = HttpResponse()
    response.headers["Cache-Control"] = "private, max-age=60"
    assert _directives(_run(SimpleNamespace(is_authenticated=True), response)) == {
        "private",
        "max-age=60",
    }


def test_anonymous_responses_are_left_alone() -> None:
    assert "Cache-Control" not in _run(SimpleNamespace(is_authenticated=False), HttpResponse())


def test_a_request_without_a_user_is_left_alone() -> None:
    assert "Cache-Control" not in _run(None, HttpResponse()).headers


def test_without_a_session_cookie_the_user_is_never_looked_up() -> None:
    class Untouchable:
        @property
        def is_authenticated(self) -> bool:
            raise AssertionError("the user (and the session) must not be read")

    response = _run(Untouchable(), HttpResponse(), cookie=False)
    assert "Cache-Control" not in response.headers
