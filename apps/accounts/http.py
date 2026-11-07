"""Request helpers shared by the MFA views, decorators and admin (CORE.4)."""

from django.conf import settings
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core.audit import AuditContext
from apps.core.models import AuditLog

from .engine import RelyingParty


def audit_context(request: HttpRequest) -> AuditContext:
    """Who acts on their own account, or on someone else's from the admin."""
    user = request.user
    staff = bool(getattr(user, "is_staff", False))
    return AuditContext(
        actor_id=user.pk,
        actor_label=f"{'staff' if staff else 'user'}:{user.get_username()}",
        source=AuditLog.Source.ADMIN if staff else AuditLog.Source.SYSTEM,
        ip=request.META.get("REMOTE_ADDR"),
    )


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
