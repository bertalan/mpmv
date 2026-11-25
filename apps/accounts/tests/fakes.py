"""Test doubles for the external boundaries of accounts (ADR-0006: mock only through our own
interfaces). The real Turnstile API is never called by the test suite."""

import secrets
from typing import ClassVar


class FakeCaptchaVerifier:
    """A `CaptchaVerifier` that accepts exactly one token, generated per test run.

    Every call is remembered in `calls` as `(token, ip)` so tests can assert what reached the
    verifier. Reset it with `FakeCaptchaVerifier.reset()`.
    """

    VALID_TOKEN: ClassVar[str] = secrets.token_urlsafe(16)
    calls: ClassVar[list[tuple[str, str | None]]] = []

    def verify(self, token: str, *, ip: str | None) -> bool:
        type(self).calls.append((token, ip))
        return token == self.VALID_TOKEN

    @classmethod
    def reset(cls) -> None:
        cls.calls.clear()


class FakeBreachChecker:
    """CORE.28: a `BreachedPasswordChecker` with no network.

    Tests put passwords in `breached` (generated per test, never a real leaked one) or set `down`.
    `reset()` runs before every test (root conftest).
    """

    breached: ClassVar[set[str]] = set()
    down: ClassVar[bool] = False
    calls: ClassVar[list[str]] = []

    def is_breached(self, password: str) -> bool:
        from apps.accounts.breach import BreachCheckUnavailable

        type(self).calls.append(password)
        if type(self).down:
            raise BreachCheckUnavailable("fake provider down")
        return password in type(self).breached

    @classmethod
    def reset(cls) -> None:
        cls.breached.clear()
        cls.calls.clear()
        cls.down = False
