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
