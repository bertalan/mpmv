"""CORE.5: Cloudflare Turnstile behind our own `CaptchaVerifier` interface.

The HTTP transport is injected, so these tests never reach the real API.
"""

import secrets
from collections.abc import Mapping
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.accounts.captcha import (
    TURNSTILE_VERIFY_URL,
    CaptchaVerifier,
    TurnstileVerifier,
    get_captcha_verifier,
)
from apps.accounts.tests.fakes import FakeCaptchaVerifier

FAKE = "apps.accounts.tests.fakes.FakeCaptchaVerifier"


class RecordingTransport:
    def __init__(self, answer: Mapping[str, Any] | Exception) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, data: Mapping[str, str], timeout: float) -> Mapping[str, Any]:
        self.requests.append((url, dict(data), timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_turnstile_posts_secret_token_and_client_ip_to_siteverify() -> None:
    secret, token = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    transport = RecordingTransport({"success": True})
    verifier = TurnstileVerifier(secret=secret, transport=transport)
    assert verifier.verify(token, ip="203.0.113.7") is True
    [(url, data, timeout)] = transport.requests
    assert url == TURNSTILE_VERIFY_URL
    assert data == {"secret": secret, "response": token, "remoteip": "203.0.113.7"}
    assert 0 < timeout <= 10


def test_turnstile_rejects_a_token_the_api_refuses() -> None:
    transport = RecordingTransport({"success": False, "error-codes": ["invalid-input-response"]})
    verifier = TurnstileVerifier(secret=secrets.token_urlsafe(16), transport=transport)
    assert verifier.verify(secrets.token_urlsafe(16), ip=None) is False
    assert "remoteip" not in transport.requests[0][1]


@pytest.mark.parametrize("token", ["", "   "])
def test_turnstile_rejects_a_missing_token_without_calling_the_api(token: str) -> None:
    transport = RecordingTransport({"success": True})
    verifier = TurnstileVerifier(secret=secrets.token_urlsafe(16), transport=transport)
    assert verifier.verify(token, ip="203.0.113.7") is False
    assert transport.requests == []


@pytest.mark.parametrize(
    "answer", [OSError("network down"), ValueError("not JSON"), {"success": "true"}, {}]
)
def test_turnstile_fails_closed_when_the_api_errors_or_answers_oddly(
    answer: Mapping[str, Any] | Exception,
) -> None:
    verifier = TurnstileVerifier(
        secret=secrets.token_urlsafe(16), transport=RecordingTransport(answer)
    )
    assert verifier.verify(secrets.token_urlsafe(16), ip=None) is False


@override_settings(TURNSTILE_SECRET_KEY="")
def test_turnstile_without_a_secret_is_a_configuration_error() -> None:
    with pytest.raises(ImproperlyConfigured, match="TURNSTILE_SECRET_KEY"):
        TurnstileVerifier()


@override_settings(CAPTCHA_ENABLED=False, CAPTCHA_VERIFIER=FAKE)
def test_no_verifier_when_captcha_is_disabled() -> None:
    assert get_captcha_verifier() is None


@override_settings(CAPTCHA_ENABLED=True, CAPTCHA_VERIFIER=FAKE)
def test_the_configured_verifier_is_used_when_captcha_is_enabled() -> None:
    verifier = get_captcha_verifier()
    assert isinstance(verifier, FakeCaptchaVerifier)
    assert isinstance(verifier, CaptchaVerifier)  # satisfies our protocol
