"""CORE.28: the breached-password check. No real network: the HTTP transport is injected."""

import hashlib
import secrets
from collections.abc import Mapping

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.accounts.breach import (
    BreachCheckUnavailable,
    BreachedPasswordChecker,
    BreachedPasswordValidator,
    HibpRangeChecker,
    get_breach_checker,
    password_is_breached,
)
from apps.accounts.tests.fakes import FakeBreachChecker


def _split(password: str) -> tuple[str, str]:
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    return digest[:5], digest[5:]


class Recorder:
    def __init__(self, body: str | Exception) -> None:
        self.body = body
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> str:
        self.requests.append((url, dict(headers), timeout))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_only_the_five_character_prefix_leaves_the_server() -> None:
    password = secrets.token_urlsafe(16)
    prefix, suffix = _split(password)
    transport = Recorder(f"{'A' * 35}:3\r\n{suffix}:12\r\n")
    checker = HibpRangeChecker(transport=transport, base_url="https://x.test/range/")
    assert checker.is_breached(password) is True
    ((url, headers, timeout),) = transport.requests
    assert url.endswith("/range/" + prefix)
    assert suffix not in url
    assert password not in url
    assert headers == {"Add-Padding": "true"}
    assert 0 < timeout <= 5


def test_a_missing_suffix_is_not_breached_and_padding_rows_are_ignored() -> None:
    password = secrets.token_urlsafe(16)
    _prefix, suffix = _split(password)
    assert HibpRangeChecker(transport=Recorder("0" * 35 + ":4\r\n")).is_breached(password) is False
    assert HibpRangeChecker(transport=Recorder(f"{suffix}:0\r\n")).is_breached(password) is False


@pytest.mark.parametrize("error", [TimeoutError("slow"), OSError("down"), ValueError("bad")])
def test_a_transport_failure_raises_unavailable_without_leaking_the_hash(error: Exception) -> None:
    password = secrets.token_urlsafe(16)
    with pytest.raises(BreachCheckUnavailable) as caught:
        HibpRangeChecker(transport=Recorder(error)).is_breached(password)
    assert _split(password)[0] not in str(caught.value)


def test_fail_open_when_the_provider_is_down() -> None:
    FakeBreachChecker.down = True
    assert password_is_breached(secrets.token_urlsafe(12)) is False


def test_the_fake_reports_breached_passwords() -> None:
    password = secrets.token_urlsafe(12)
    FakeBreachChecker.breached.add(password)
    assert password_is_breached(password) is True


@override_settings(BREACH_CHECK_ENABLED=False)
def test_disabled_check_never_calls_the_provider() -> None:
    assert get_breach_checker() is None
    assert password_is_breached(secrets.token_urlsafe(8)) is False
    assert FakeBreachChecker.calls == []


def test_fake_and_real_client_meet_the_interface() -> None:
    assert isinstance(FakeBreachChecker(), BreachedPasswordChecker)
    assert isinstance(HibpRangeChecker(transport=Recorder("")), BreachedPasswordChecker)


def test_validator_refuses_a_breached_password_and_passes_when_down() -> None:
    password = secrets.token_urlsafe(16)
    FakeBreachChecker.breached.add(password)
    with pytest.raises(ValidationError) as caught:
        BreachedPasswordValidator().validate(password)
    assert caught.value.code == "password_breached"
    FakeBreachChecker.down = True
    BreachedPasswordValidator().validate(password)
    BreachedPasswordValidator().validate(secrets.token_urlsafe(16))


def test_a_lone_surrogate_fails_open_not_with_a_500() -> None:
    checker = HibpRangeChecker(transport=Recorder(""))
    with pytest.raises(BreachCheckUnavailable):
        checker.is_breached("abc\ud800def")


def test_an_oversized_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.accounts import breach

    class Huge:
        def __enter__(self) -> "Huge":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b"A" * size

    monkeypatch.setattr(breach, "open_url", lambda request, timeout: Huge())
    with pytest.raises(ValueError, match="too large"):
        breach._get_text("https://x.test/range/ABCDE", {}, 1.0)


@pytest.mark.parametrize(
    ("url", "checker", "errors"),
    [
        ("http://x.test/range/", "apps.accounts.breach.HibpRangeChecker", 1),
        ("file:///etc/passwd", "apps.accounts.breach.HibpRangeChecker", 1),
        ("https://x.test/range/", "apps.accounts.breach.HibpRangeChecker", 0),
        ("http://127.0.0.1:9/", "apps.accounts.tests.fakes.FakeBreachChecker", 0),
    ],
)
def test_the_real_client_needs_an_https_url(url: str, checker: str, errors: int) -> None:
    from apps.accounts.checks import check_breach_check_url

    with override_settings(BREACH_CHECK_URL=url, BREACH_CHECKER=checker):
        assert len(check_breach_check_url(None)) == errors
