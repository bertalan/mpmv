"""Breached-password check (CORE.28, owner decision Q-11): the Have I Been Pwned range API with
k-anonymity behind our own `BreachedPasswordChecker` interface.

Only the first five hex characters of the password's SHA-1 leave the server; the matching
suffixes come back and are compared locally. The password and the full hash are never logged,
stored or sent. The request asks for padding (`Add-Padding: true`) so the response size does not
reveal how many hashes share the prefix. If the service is down or slow the check fails open: the
caller continues and the check runs again next time (`password_is_breached` returns False).
"""

import hashlib
import logging
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from apps.core.outbound import open_url

logger = logging.getLogger(__name__)

HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"
PREFIX_LENGTH = 5
# A real answer is about 40 KB; anything bigger is refused (a hostile or broken endpoint).
MAX_BODY_BYTES = 1_000_000

# (url, headers, timeout in seconds) -> response body text.
Transport = Callable[[str, Mapping[str, str], float], str]


class BreachCheckUnavailable(Exception):
    """The provider could not answer (network error, timeout, bad status or body)."""


@runtime_checkable
class BreachedPasswordChecker(Protocol):
    def is_breached(self, password: str) -> bool:
        """True if the password appears in a known breach.

        Raises `BreachCheckUnavailable` when the provider cannot answer.
        """
        ...


def _get_text(url: str, headers: Mapping[str, str], timeout: float) -> str:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310 (fixed https URL)
    with open_url(request, timeout=timeout) as response:  # never follows a redirect
        body: bytes = response.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("breach API answer too large")
    return body.decode("ascii", errors="replace")


class HibpRangeChecker:
    """k-anonymity lookup on the HIBP range API (`GET /range/<first 5 SHA-1 hex chars>`)."""

    TIMEOUT_SECONDS = 2.0

    def __init__(self, *, transport: Transport | None = None, base_url: str | None = None) -> None:
        self._transport = transport or _get_text
        self._base_url = base_url or str(settings.BREACH_CHECK_URL or HIBP_RANGE_URL)

    def is_breached(self, password: str) -> bool:
        try:
            digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
            prefix, suffix = digest[:PREFIX_LENGTH], digest[PREFIX_LENGTH:]
            body = self._transport(
                self._base_url + prefix, {"Add-Padding": "true"}, self.TIMEOUT_SECONDS
            )
        except (OSError, ValueError) as exc:
            # The log names the error class only: the URL carries the hash prefix.
            raise BreachCheckUnavailable(type(exc).__name__) from None
        for line in body.splitlines():
            candidate, _sep, count = line.strip().partition(":")
            if candidate.strip().upper() != suffix:
                continue
            # A padded entry has a count of 0: it is noise, not a match.
            return count.strip().isdigit() and int(count) > 0
        return False


def get_breach_checker() -> BreachedPasswordChecker | None:
    """The configured checker, or None when the check is disabled."""
    if not settings.BREACH_CHECK_ENABLED:
        return None
    checker: BreachedPasswordChecker = import_string(settings.BREACH_CHECKER)()
    return checker


def password_is_breached(password: str) -> bool:
    """True only when the provider confirms a breach; disabled or down is False (fail open)."""
    checker = get_breach_checker()
    if checker is None or not password:
        return False
    try:
        return checker.is_breached(password)
    except BreachCheckUnavailable as exc:
        logger.warning("Breached-password check unavailable (%s); continuing without it", exc)
        return False


class BreachedPasswordValidator:
    """Django password validator: refuse a password found in a breach (the set path)."""

    code = "password_breached"

    def validate(self, password: str, user: Any = None) -> None:
        if password_is_breached(password):
            raise ValidationError(
                _(
                    "This password has appeared in a known data breach. "
                    "Please choose a different one."
                ),
                code=self.code,
            )

    def get_help_text(self) -> str:
        return str(_("Your password must not appear in a known data breach."))
