"""Bot protection for login and registration forms (CORE.5): Cloudflare Turnstile behind our
own `CaptchaVerifier` interface, so tests and a future provider plug in without touching views.

Disabled unless `settings.CAPTCHA_ENABLED` is true; keys come from the environment
(`TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`). Every form that ends in `authenticate()` or in
`protect_registration` must render `{% captcha_widget %}` (see templatetags/captcha.py).
"""

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.utils.module_loading import import_string

from apps.core.http import client_ip

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js"
# The name of the hidden input the widget adds to the form (a field name, not a secret).
TOKEN_FIELD = "cf-turnstile-response"  # noqa: S105

Transport = Callable[[str, Mapping[str, str], float], Mapping[str, Any]]


@runtime_checkable
class CaptchaVerifier(Protocol):
    def verify(self, token: str, *, ip: str | None) -> bool:
        """True only if the provider confirms the token; any doubt is a refusal."""
        ...


def _post_form(url: str, data: Mapping[str, str], timeout: float) -> Mapping[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310 (fixed https URL)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        answer = json.loads(response.read())
    if not isinstance(answer, dict):
        raise ValueError("siteverify did not answer with a JSON object")
    return answer


class TurnstileVerifier:
    """Server-side validation of a Turnstile token (siteverify API). Fails closed."""

    TIMEOUT_SECONDS = 5.0

    def __init__(self, secret: str | None = None, *, transport: Transport | None = None) -> None:
        self._secret = secret if secret is not None else settings.TURNSTILE_SECRET_KEY
        if not self._secret:
            raise ImproperlyConfigured("TurnstileVerifier needs TURNSTILE_SECRET_KEY")
        self._transport = transport or _post_form

    def verify(self, token: str, *, ip: str | None) -> bool:
        token = token.strip()
        if not token:
            return False
        data = {"secret": self._secret, "response": token}
        if ip:
            data["remoteip"] = ip
        try:
            answer = self._transport(TURNSTILE_VERIFY_URL, data, self.TIMEOUT_SECONDS)
        except (OSError, ValueError):
            logger.warning("Turnstile siteverify failed; refusing the token", exc_info=True)
            return False
        if answer.get("success") is not True:
            logger.info("Turnstile refused a token: %s", answer.get("error-codes"))
            return False
        return True


def get_captcha_verifier() -> CaptchaVerifier | None:
    """The configured verifier, or None when the captcha is disabled."""
    if not settings.CAPTCHA_ENABLED:
        return None
    verifier: CaptchaVerifier = import_string(settings.CAPTCHA_VERIFIER)()
    return verifier


def captcha_passes(request: HttpRequest) -> bool:
    """True when the captcha is disabled or the request carries a confirmed token."""
    verifier = get_captcha_verifier()
    if verifier is None:
        return True
    return verifier.verify(request.POST.get(TOKEN_FIELD, ""), ip=client_ip(request))
