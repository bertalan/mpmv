"""SEC.5 follow-up: the Turnstile transport never follows a redirect (local server, no network)."""

import secrets

import pytest

from apps.accounts import captcha
from apps.core.outbound import RedirectRefused
from apps.core.tests.redirect_server import redirect_server


def test_post_form_refuses_a_redirect_to_another_host() -> None:
    with redirect_server() as server, pytest.raises(RedirectRefused):
        captcha._post_form(f"{server.base_url}/siteverify", {"response": "x"}, 5)
    assert server.paths == ["/siteverify"]


def test_the_verifier_fails_closed_on_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    with redirect_server() as server:
        monkeypatch.setattr(captcha, "TURNSTILE_VERIFY_URL", f"{server.base_url}/siteverify")
        verifier = captcha.TurnstileVerifier(secret=secrets.token_urlsafe(16))
        assert verifier.verify(secrets.token_urlsafe(16), ip=None) is False
    assert server.paths == ["/siteverify"]
