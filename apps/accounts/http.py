"""Request helpers shared by the MFA views, decorators and admin (CORE.4)."""

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core.audit import AuditContext
from apps.core.http import client_ip
from apps.core.models import AuditLog

from .engine import RelyingParty


def user_context(user: AbstractBaseUser | AnonymousUser, ip: str | None) -> AuditContext:
    """`user` acting on their own account, or on someone else's from the admin."""
    staff = bool(getattr(user, "is_staff", False))
    return AuditContext(
        actor_id=user.pk,
        actor_label=f"{'staff' if staff else 'user'}:{user.get_username()}",
        source=AuditLog.Source.ADMIN if staff else AuditLog.Source.SYSTEM,
        ip=ip,
    )


def audit_context(request: HttpRequest) -> AuditContext:
    """Who acts, from the request: the signed-in user and the client IP (CORE.5 rules)."""
    return user_context(request.user, client_ip(request))


def safe_next(request: HttpRequest, default: str = "/") -> str:
    """The `next` URL from the query string or form, if it stays on this site."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return default


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
