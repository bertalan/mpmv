"""ApiClient lifecycle and request authentication (CORE.6, CORE.13).

Rotation replaces the key immediately: the old key stops working as soon as the rotation
commits (no grace period). A pipeline rotates by deploying the new key right after receiving it.

A vendor key (CORE.13) is issued only by platform staff holding `api.add_apiclient` or an owner
of that vendor; a platform key only by such staff, or by trusted code without an issuer (shell,
deploy scripts).
"""

import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation
from apps.vendors.engine import VendorAction
from apps.vendors.models import Vendor
from apps.vendors.services import has_vendor_permission

from .engine import (
    AuthStatus,
    Decision,
    GeneratedKey,
    InvalidApiClient,
    Reason,
    deny,
    evaluate,
    generate_key,
    hash_key,
    key_matches,
    normalize_allowed_ips,
    parse_authorization,
    validate_scopes,
)
from .models import ApiClient, ApiMutationLog

logger = logging.getLogger(__name__)

MODEL = "api.apiclient"
# The model permission platform staff need to issue a key (`is_staff` alone is admin login only).
ISSUE_PERMISSION = "api.add_apiclient"
_PREFIX_ATTEMPTS = 5
# Compared against when the prefix is unknown, so both failure paths do the same work.
_DUMMY_HASH = hash_key("")


@dataclass(frozen=True)
class IssuedKey:
    """Result of issue/rotate. `key` is the full key, shown once; None on dry-run."""

    changeset: ChangeSet
    client: ApiClient | None
    key: str | None


@dataclass(frozen=True)
class AuthResult:
    decision: Decision
    client: ApiClient | None  # set when the key is valid (200 or 403), None on 401

    @property
    def status(self) -> AuthStatus:
        return self.decision.status

    @property
    def reason(self) -> Reason | None:
        return self.decision.reason

    @property
    def http_status(self) -> int:
        return self.decision.http_status

    @property
    def ok(self) -> bool:
        return self.decision.status is AuthStatus.OK


def _new_key() -> GeneratedKey:
    """A key whose prefix is not taken (the unique constraint is the final guard)."""
    for _ in range(_PREFIX_ATTEMPTS):
        generated = generate_key()
        if not ApiClient.objects.filter(key_prefix=generated.prefix).exists():
            return generated
    raise RuntimeError("could not generate a unique API key prefix")


def _future(expires_at: dt.datetime) -> dt.datetime:
    if expires_at <= timezone.now():
        raise InvalidApiClient("expires_at must be in the future")
    return expires_at


def check_issuer(issuer: AbstractBaseUser | AnonymousUser | None, vendor: Vendor | None) -> None:
    """Raise PermissionDenied unless `issuer` may issue a key for `vendor` (None = platform).

    Platform staff holding `api.add_apiclient` issue any key (a platform key reaches every
    vendor's rows, so `is_staff` alone, i.e. admin login, is not enough); an owner of a vendor
    issues that vendor's keys only. Without an issuer (trusted code: shell, deploy scripts) only
    platform keys can be issued.
    """
    if issuer is None:
        if vendor is not None:
            raise PermissionDenied("a vendor API key needs an issuer")
        return
    if not issuer.is_authenticated or not issuer.is_active:
        raise PermissionDenied("the issuer is not an active user")
    if isinstance(issuer, User) and issuer.is_staff and issuer.has_perm(ISSUE_PERMISSION):
        return
    if vendor is None:
        raise PermissionDenied("only platform staff with api.add_apiclient issue platform API keys")
    if not isinstance(issuer, User) or not has_vendor_permission(
        issuer, vendor, VendorAction.API_KEYS
    ):
        raise PermissionDenied("only platform staff or an owner of the vendor issue its API keys")


def issue_api_client(
    *,
    name: str,
    scopes: Iterable[str],
    expires_at: dt.datetime,
    allowed_ips: Iterable[str] | None = None,
    vendor: Vendor | None = None,
    issuer: AbstractBaseUser | AnonymousUser | None = None,
    ctx: AuditContext,
    dry_run: bool = False,
) -> IssuedKey:
    """Issue a key: for `vendor` (a vendor key) or, without one, a platform key.

    `issuer` is the user asking for it (see `check_issuer`); the audit entry names the vendor.
    """
    check_issuer(issuer, vendor)
    if not name.strip():
        raise InvalidApiClient("name is required")
    generated = _new_key()
    client = ApiClient(
        vendor=vendor,
        name=name.strip(),
        scopes=validate_scopes(scopes),
        key_prefix=generated.prefix,
        key_hash=hash_key(generated.key),
        expires_at=_future(expires_at),
        allowed_ips=normalize_allowed_ips(allowed_ips),
    )
    changeset = ChangeSet()
    changeset.create(MODEL, str(client.public_id), client.snapshot())
    run_mutation(changeset, client.save, ctx=ctx, action="api.client.issued", dry_run=dry_run)
    if dry_run:
        return IssuedKey(changeset, None, None)
    return IssuedKey(changeset, client, generated.key)


def rotate_api_key(
    client: ApiClient,
    *,
    ctx: AuditContext,
    expires_at: dt.datetime | None = None,
    dry_run: bool = False,
) -> IssuedKey:
    """Replace the key (new prefix and secret); the old key is invalid immediately."""
    if not client.is_active:
        raise InvalidApiClient("a revoked API client cannot be rotated; issue a new one")
    new_expiry = _future(expires_at if expires_at is not None else client.expires_at)
    generated = _new_key()
    before = client.snapshot()
    after = {**before, "key_prefix": generated.prefix, "expires_at": new_expiry}
    changeset = ChangeSet()
    changeset.update(MODEL, str(client.public_id), before, after)

    def apply() -> None:
        client.key_prefix = generated.prefix
        client.key_hash = hash_key(generated.key)
        client.expires_at = new_expiry
        client.save(update_fields=["key_prefix", "key_hash", "expires_at", "updated_at"])

    run_mutation(changeset, apply, ctx=ctx, action="api.client.rotated", dry_run=dry_run)
    if dry_run:
        return IssuedKey(changeset, None, None)
    return IssuedKey(changeset, client, generated.key)


def revoke_api_client(client: ApiClient, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Disable the client for good; revoking an already revoked client changes nothing."""
    changeset = ChangeSet()
    if not client.is_active:
        return changeset
    now = timezone.now()
    before = client.snapshot()
    changeset.update(
        MODEL, str(client.public_id), before, {**before, "is_active": False, "revoked_at": now}
    )

    def apply() -> None:
        client.is_active = False
        client.revoked_at = now
        client.save(update_fields=["is_active", "revoked_at", "updated_at"])

    return run_mutation(changeset, apply, ctx=ctx, action="api.client.revoked", dry_run=dry_run)


def authenticate(
    authorization: str | None,
    *,
    ip: str | None,
    required_scope: str | None,
    now: dt.datetime | None = None,
) -> AuthResult:
    """Check an `Authorization` header: 401 for bad/expired/revoked keys, 403 for IP or scope."""
    now = now or timezone.now()
    parsed = parse_authorization(authorization)
    if isinstance(parsed, Decision):
        return AuthResult(parsed, None)
    client = ApiClient.objects.select_related("vendor").filter(key_prefix=parsed.prefix).first()
    matches = key_matches(parsed.key, client.key_hash if client else _DUMMY_HASH)
    if client is None or not matches:
        logger.info("API key refused: prefix=%s reason=%s", parsed.prefix, Reason.INVALID)
        return AuthResult(deny(Reason.INVALID), None)
    decision = evaluate(client.state(), now=now, ip=ip, required_scope=required_scope)
    if decision.status is AuthStatus.UNAUTHENTICATED:
        logger.info("API key refused: prefix=%s reason=%s", parsed.prefix, decision.reason)
        return AuthResult(decision, None)
    if decision.status is AuthStatus.FORBIDDEN:
        logger.info("API key forbidden: prefix=%s reason=%s", parsed.prefix, decision.reason)
        return AuthResult(decision, client)
    ApiClient.objects.filter(pk=client.pk).update(last_used_at=now)
    client.last_used_at = now
    return AuthResult(decision, client)


# --- Mutation log (CORE.7)


def record_mutation_call(
    client: ApiClient,
    *,
    request_id: str,
    method: str,
    path: str,
    route: str,
    dry_run: bool,
    status_code: int,
    ip: str | None,
    duration_ms: int,
) -> ApiMutationLog:
    """Store one mutating API call by a known client (see ApiMutationLog for the scope)."""
    return ApiMutationLog.objects.create(
        request_id=request_id,
        client_public_id=client.public_id,
        client_name=client.name,
        key_prefix=client.key_prefix,
        method=method.upper(),
        path=path[:500],
        route=route[:500],
        dry_run=dry_run,
        status_code=status_code,
        ip=ip,
        duration_ms=max(duration_ms, 0),
    )
