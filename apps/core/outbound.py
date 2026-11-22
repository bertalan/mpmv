"""Outbound HTTP (SEC.5 follow-up): one urllib opener that never follows a redirect.

Every adapter that calls a provider (payments, VIES, captcha, geocoding, map tiles) opens its
request here. A 3xx answer is never followed, to the same host or another one: it raises
`RedirectRefused`, an `HTTPError` subclass, so each adapter's existing error handling treats it
like any other HTTP error status. The log line names the status and the host only; the URL may
carry a key or personal data in its path or query string, so it never appears in a log or an
exception. Timeouts stay with the caller.
"""

import logging
import urllib.error
import urllib.request
from email.message import Message
from typing import IO, Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


class RedirectRefused(urllib.error.HTTPError):
    """The provider answered with a redirect; it was not followed."""

    def __init__(self, url: str, code: int, headers: Message) -> None:
        super().__init__(_origin(url), code, f"redirect refused (HTTP {code})", headers, None)


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        fp.close()
        logger.warning(
            "Outbound redirect refused: HTTP %s from %s", code, urlsplit(req.full_url).hostname
        )
        raise RedirectRefused(req.full_url, code, headers)


def no_redirect_opener(*handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """An opener that raises `RedirectRefused` on any followable 3xx; other 3xx statuses are
    already `HTTPError`s in urllib. `handlers` are extra handlers (tests use them)."""
    return urllib.request.build_opener(_RefuseRedirect(), *handlers)


def open_url(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open `request` without following any redirect; returns urllib's response object."""
    return no_redirect_opener().open(request, timeout=timeout)
