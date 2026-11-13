"""Request helpers shared by the MFA views, decorators and admin (CORE.4)."""

from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.http import HttpRequest
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme

from .engine import RelyingParty


def safe_next(request: HttpRequest, default: str = "/") -> str:
    """The `next` URL from the query string or form, if it stays on this site."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return default


# Pages that must never be the target of a `next` taken from a Referer: sign-in and sign-out
# (of the Manager and of both admins) and the MFA pages themselves (a loop).
_AUTH_VIEW_NAMES = frozenset(
    {"login", "logout", "admin:login", "admin:logout", "wagtailadmin_login", "wagtailadmin_logout"}
)
# One-shot token URLs are never re-sent in a `next`.
_AUTH_NAMESPACES = frozenset({"mfa", "action_link"})


def _is_auth_page(path: str) -> bool:
    try:
        match = resolve(path)
    except Resolver404:
        return False
    return match.view_name in _AUTH_VIEW_NAMES or bool(_AUTH_NAMESPACES & set(match.namespaces))


def return_url(request: HttpRequest, default: str = "/") -> str:
    """Where to send a user after a second-factor check turned this request away.

    A GET or HEAD comes back to itself. Any other method cannot be replayed (the endpoint may
    answer 405 to a GET), so the same-origin Referer is used, if it is accepted by
    `url_has_allowed_host_and_scheme` and is not a sign-in, sign-out or MFA page; else `default`.
    """
    if request.method in ("GET", "HEAD"):
        return request.get_full_path()
    referer = request.headers.get("Referer", "")
    if not referer or not url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return default
    parts = urlsplit(referer)
    decoded = unquote(parts.path)
    if (
        not decoded.startswith("/")
        or "//" in decoded
        or "\\" in decoded
        or any(ord(char) < 32 or ord(char) == 127 for char in referer)
        or _is_auth_page(decoded)
    ):
        return default
    return f"{parts.path}?{parts.query}" if parts.query else parts.path or default


def relying_party(request: HttpRequest) -> RelyingParty:
    """WebAuthn relying party for this request: the host validated by ALLOWED_HOSTS."""
    host = request.get_host()
    return RelyingParty(
        id=host.rsplit(":", 1)[0] if not host.endswith("]") else host,
        name=settings.MFA_ISSUER,
        origin=f"{request.scheme}://{host}",
    )


def wants_json(request: HttpRequest) -> bool:
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept
