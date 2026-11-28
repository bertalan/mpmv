"""ING.5 / ING.14: the one outbound HTTP call of every AI provider adapter (Gemini, Claude, OpenAI).

The hosts are fixed constants of the adapters (generativelanguage.googleapis.com, api.anthropic.com,
api.openai.com); nothing a vendor types reaches the URL. The key is a header, the text a body.
No redirect is followed (`apps.core.outbound.open_url`). Errors name the failure only.
"""

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from django.views.decorators.debug import sensitive_variables

from apps.core.outbound import open_url

from .enrichment import ProviderError

MAX_RESPONSE_BYTES = 1_000_000
RETRY_DELAYS = (1.0, 3.0)  # seconds before the 2nd and 3rd attempt, on 429 and 5xx only

Transport = Callable[[str, Mapping[str, Any], Mapping[str, str], float], Any]


class RetryableProviderError(ProviderError):
    """A rate limit or a server error: worth another try after a pause."""


class KeyHolder:
    """Base of the adapters that hold an API key: they cannot be pickled or copied, so the key can
    never become the argument of a queued job. A job carries the vendor and draft ids and
    resolves the key inside the worker (`provider_services.resolve_enricher`)."""

    def __reduce__(self) -> Any:
        raise TypeError("an enricher holds an API key: queue ids, not the enricher")


@sensitive_variables("headers")  # the frame holds the API key
def http_post_json(
    url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
) -> Any:
    """POST ``payload`` as JSON and return the decoded answer. Errors name the failure only.

    TODO(MIG.12): move to ``apps/core/safe_http.py`` once it is on main.
    """
    request = urllib.request.Request(  # noqa: S310 (fixed https endpoint)
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with open_url(request, timeout=timeout) as response:  # never follows a redirect
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise RetryableProviderError(f"HTTP {exc.code}") from None
        raise ProviderError(f"HTTP {exc.code}") from None
    except (OSError, http.client.HTTPException) as exc:  # sockets, timeouts, IncompleteRead...
        raise ProviderError(type(exc).__name__) from None  # never str(exc): it can hold the body
    except Exception as exc:
        raise ProviderError(type(exc).__name__) from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProviderError("response too large")
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderError("invalid JSON") from None
