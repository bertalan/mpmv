"""ApiClient lifecycle and request authentication (CORE.6, CORE.13).

Rotation replaces the key immediately: the old key stops working as soon as the rotation
commits (no grace period). A pipeline rotates by deploying the new key right after receiving it.

A vendor key (CORE.13) is issued only by platform staff holding `api.add_apiclient` or an owner
of that vendor; a platform key only by such staff, or by trusted code without an issuer (shell,
deploy scripts).

Vendor key policy (CORE.14): a vendor key is issued only for an active vendor and holds only
`engine.VENDOR_GRANTABLE_SCOPES`, checked again on every request; only a key of an active vendor
writes (403 for applicant, onboarding and suspended vendors, which only read); a key of a closed
vendor is refused (401); deleting a vendor audits each of its keys (`vendor_key_deletions`).

Rate limit (CORE.12): `count_api_call` counts each authenticated call on a PostgreSQL counter
shared by every app process (`ApiRateCounter`); limits come from the `API_RATE_LIMIT*` settings
and, for one key, from the override staff set with `set_api_client_rate_limit` (CORE.16), which
wins over them (precedence in `engine.limits_for`).
"""

import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation
from apps.vendors import engine as vendor_engine
from apps.vendors.engine import VendorAction
from apps.vendors.models import Vendor
from apps.vendors.services import has_vendor_permission

from .engine import (
    AuthStatus,
    Decision,
    GeneratedKey,
    InvalidApiClient,
    InvalidRateLimit,
    Rate,
    RateDecision,
    RatePolicy,
    Reason,
    combine_decisions,
    deny,
    evaluate,
    generate_key,
    hash_key,
    key_matches,
    limits_for,
    normalize_allowed_ips,
    parse_authorization,
    parse_rate,
    platform_only_scopes,
    rate_decision,
    rate_policy,
    validate_scopes,
    window_start,
)
from .models import ApiClient, ApiMutationLog, ApiRateCounter

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


def _check_vendor_active(vendor: Vendor) -> None:
    """Vendor keys are issued only while the vendor is active (CORE.14), as stored."""
    status = Vendor.objects.filter(pk=vendor.pk).values_list("status", flat=True).first()
    if status != vendor_engine.ACTIVE:
        raise InvalidApiClient(
            f"API keys are issued only for an active vendor; this vendor is {status or 'missing'}"
        )


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
    A vendor key needs an active vendor (CORE.14): InvalidApiClient otherwise, also on dry-run.
    """
    check_issuer(issuer, vendor)
    if vendor is not None:
        _check_vendor_active(vendor)
    if not name.strip():
        raise InvalidApiClient("name is required")
    generated = _new_key()
    client = ApiClient(
        vendor=vendor,
        name=name.strip(),
        scopes=validate_scopes(scopes, vendor_key=vendor is not None),
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
    write: bool = True,
    now: dt.datetime | None = None,
) -> AuthResult:
    """Check an `Authorization` header: 401 for bad/expired/revoked keys and closed vendors,
    403 for IP, scope, or a write by the key of a vendor that is not active (CORE.14).

    `write` says whether the call changes state; a caller that does not say is treated as
    writing, so a key of a vendor that is not active is refused unless the call is a read.
    The vendor status is read with the key on every call: suspending, reinstating or closing a
    vendor takes effect on its keys at once.
    """
    now = now or timezone.now()
    parsed = parse_authorization(authorization)
    if isinstance(parsed, Decision):
        return AuthResult(parsed, None)
    client = ApiClient.objects.select_related("vendor").filter(key_prefix=parsed.prefix).first()
    matches = key_matches(parsed.key, client.key_hash if client else _DUMMY_HASH)
    if client is None or not matches:
        logger.info("API key refused: prefix=%s reason=%s", parsed.prefix, Reason.INVALID)
        return AuthResult(deny(Reason.INVALID), None)
    decision = evaluate(client.state(), now=now, ip=ip, required_scope=required_scope, write=write)
    if decision.status is AuthStatus.UNAUTHENTICATED:
        logger.info("API key refused: prefix=%s reason=%s", parsed.prefix, decision.reason)
        return AuthResult(decision, None)
    if decision.status is AuthStatus.FORBIDDEN:
        logger.info("API key forbidden: prefix=%s reason=%s", parsed.prefix, decision.reason)
        return AuthResult(decision, client)
    ApiClient.objects.filter(pk=client.pk).update(last_used_at=now)
    client.last_used_at = now
    return AuthResult(decision, client)


# --- Vendor deletion (CORE.14)


def vendor_key_deletions(vendor: Vendor, changes: ChangeSet) -> None:
    """Add a delete per key of `vendor` to its deletion's ChangeSet: one audit entry per key.

    Named in `settings.VENDOR_DELETION_AUDIT_HOOKS` and called by
    `apps.vendors.services.delete_vendor`, which cannot import this app. The keys themselves go
    with the vendor (`on_delete=CASCADE`); revoked keys are listed too.
    """
    for client in ApiClient.objects.select_related("vendor").filter(vendor=vendor).order_by("pk"):
        changes.delete(MODEL, str(client.public_id), client.snapshot())


# --- Vendor keys holding platform-only scopes (CORE.14)


@dataclass(frozen=True)
class ScopeViolation:
    client: ApiClient
    platform_only: list[str]


def vendor_keys_with_platform_scopes() -> list[ScopeViolation]:
    """Active vendor keys that hold platform-only scopes, e.g. issued before CORE.14 classed a
    scope platform-only. Read-only: such a scope is refused at request time anyway; revoke or
    reissue the key to clean it up."""
    violations: list[ScopeViolation] = []
    clients = ApiClient.objects.select_related("vendor").filter(
        vendor__isnull=False, is_active=True
    )
    for client in clients.order_by("pk"):
        platform_only = platform_only_scopes(client.scopes)
        if platform_only:
            violations.append(ScopeViolation(client, platform_only))
    return violations


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


# --- Rate limit (CORE.12)
# An operational counter, not a domain change: no ChangeSet, dry-run or audit (like the CORE.5
# login throttles). A refused call is in the application log and, when mutating, in the
# mutation log with status 429.

_COUNTER = ApiRateCounter._meta.db_table
# One atomic statement: concurrent calls of every process serialize on the row, none is lost. A
# call that arrives with an older window (it started just before the boundary) counts in the
# newer one instead of resetting it.
_COUNT_SQL = f"""
INSERT INTO {_COUNTER} (public_id, created_at, updated_at, client_id, bucket, window_start, hits)
VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, 1)
ON CONFLICT (client_id, bucket) DO UPDATE SET
    hits = CASE WHEN EXCLUDED.window_start > {_COUNTER}.window_start THEN 1
                ELSE {_COUNTER}.hits + 1 END,
    window_start = GREATEST({_COUNTER}.window_start, EXCLUDED.window_start),
    updated_at = EXCLUDED.updated_at
RETURNING hits, window_start
"""  # noqa: S608  (only the table name is interpolated, from the model; values are parameters)


def rate_policy_from_settings() -> RatePolicy:
    """`API_RATE_LIMIT` (every key), `API_RATE_LIMIT_SCOPES`, `API_RATE_LIMIT_CLIENTS`."""
    return rate_policy(
        settings.API_RATE_LIMIT,
        settings.API_RATE_LIMIT_SCOPES,
        settings.API_RATE_LIMIT_CLIENTS,
    )


def set_api_client_rate_limit(
    client: ApiClient, rate_limit: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Store (or, with "", clear) the key-wide rate limit override of one key (CORE.16).

    It raises or lowers that key only and wins over `API_RATE_LIMIT_CLIENTS` and
    `API_RATE_LIMIT`. Audited; the admin asks for a fresh second factor before calling it.
    `InvalidRateLimit` for a value that is not a rate such as `1200/minute`.
    """
    cleaned = rate_limit.strip()
    if cleaned:
        parse_rate(cleaned)
    changeset = ChangeSet()
    before = client.snapshot()
    changeset.update(MODEL, str(client.public_id), before, {**before, "rate_limit": cleaned})

    def apply() -> None:
        client.rate_limit = cleaned
        client.save(update_fields=["rate_limit", "updated_at"])

    return run_mutation(
        changeset, apply, ctx=ctx, action="api.client.rate_limit_changed", dry_run=dry_run
    )


def rate_limit_override(client: ApiClient) -> Rate | None:
    """The key's stored override, or None. A stored value that cannot be read (written around
    the service) is ignored and logged, so the settings still limit the key."""
    if not client.rate_limit:
        return None
    try:
        return parse_rate(client.rate_limit)
    except InvalidRateLimit:
        logger.error(
            "Ignoring the unreadable rate limit override of API key prefix=%s",
            client.key_prefix,
        )
        return None


def count_api_call(
    client: ApiClient, scope: str | None, *, now: dt.datetime | None = None
) -> RateDecision:
    """Count one call of `client` to an operation requiring `scope`; say whether it may run.

    The call counts on every counter `engine.limits_for` names (refused calls included) and runs
    only if each of them allows it. Call it outside a transaction (API requests are not atomic):
    each increment commits at once, so other processes see it, and no row lock is held for the
    rest of the request.
    """
    now = now or timezone.now()
    policy = rate_policy_from_settings()
    limits = limits_for(
        policy,
        scope=scope,
        client_id=str(client.public_id),
        override=rate_limit_override(client),
    )
    decisions: list[RateDecision] = []
    with connection.cursor() as cursor:
        for bucket, rate in limits:
            cursor.execute(_COUNT_SQL, [client.pk, bucket, window_start(now, rate)])
            hits, current_start = cursor.fetchone()
            decision = rate_decision(hits=hits, window_start=current_start, rate=rate, now=now)
            if not decision.allowed:
                logger.info(
                    "API key rate limited: prefix=%s bucket=%s limit=%d/%ds",
                    client.key_prefix,
                    bucket,
                    rate.limit,
                    rate.period,
                )
            decisions.append(decision)
    return combine_decisions(decisions)
