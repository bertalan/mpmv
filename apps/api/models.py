"""API keys for API v1 and MCP (CORE.6, spec 02 §2.2), the API mutation log (CORE.7) and the
per-key rate limit counters (CORE.12)."""

import uuid
from typing import Any, ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from .engine import KEY_WIDE_BUCKET, PREFIX_LENGTH, ClientState


class ApiClient(BaseModel):
    """An API key holder: pipelines, MCP, integrations.

    Only the public prefix and the SHA-256 of the key are stored; the full key is shown once at
    issuance or rotation.

    `vendor` (CORE.13): a vendor key acts for that vendor only; every API v1 operation scopes
    vendor data with `for_actor(client)` (CORE.3), so another vendor's objects answer 404. Null
    means a platform key, limited only by its scopes. The keys of a deleted vendor are deleted
    with it: a vendor key never becomes a platform key.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="api_clients",
        help_text="The vendor the key acts for; empty for a platform key.",
    )
    name = models.CharField(max_length=100)
    scopes = ArrayField(models.CharField(max_length=64))
    key_prefix = models.CharField(max_length=PREFIX_LENGTH, unique=True)
    key_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    allowed_ips = ArrayField(
        models.CharField(max_length=43),
        null=True,
        blank=True,
        help_text="CIDR networks the key may be used from; null means any address.",
    )
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    rate_limit = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "Key-wide rate limit of this key, e.g. 1200/minute, set by staff (CORE.16); it wins "
            "over API_RATE_LIMIT_CLIENTS and API_RATE_LIMIT. Empty means the settings decide."
        ),
    )

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.key_prefix})"

    @property
    def credential_vendor_id(self) -> int | None:
        """The actor scope of the key (`apps.vendors.scoping.Credential`): None = platform."""
        return self.vendor_id

    def state(self) -> ClientState:
        """What the authorization decision needs, the vendor's current status included."""
        return ClientState(
            is_active=self.is_active,
            expires_at=self.expires_at,
            scopes=tuple(self.scopes),
            allowed_ips=None if self.allowed_ips is None else tuple(self.allowed_ips),
            vendor_status=None if self.vendor is None else self.vendor.status,
        )

    def snapshot(self) -> dict[str, Any]:
        """Audit/ChangeSet view: everything but the key hash; `vendor` is the vendor's public id."""
        return {
            "vendor": None if self.vendor is None else str(self.vendor.public_id),
            "name": self.name,
            "scopes": list(self.scopes),
            "key_prefix": self.key_prefix,
            "expires_at": self.expires_at,
            "allowed_ips": None if self.allowed_ips is None else list(self.allowed_ips),
            "is_active": self.is_active,
            "revoked_at": self.revoked_at,
            "rate_limit": self.rate_limit,
        }


class ApiMutationLog(models.Model):
    """One mutating API call (POST/PUT/PATCH/DELETE) by a known client: who, which key, what.

    Complements AuditLog rather than replacing it: AuditLog records each applied domain change
    (before/after), whatever the channel; this log records the *call*, including dry-runs,
    refusals (403) and failures, which change nothing and so leave no AuditLog row. The two
    join on `request_id`. Calls without a valid key are not stored (anonymous traffic must not
    be able to fill the table); they go to the application log.

    The client is stored by public id, name and key prefix, not as a foreign key, so rows
    outlive the client. Request bodies and query strings are not stored (they may carry
    personal data). Append-only: save guard plus a database trigger.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    request_id = models.CharField(max_length=64, db_index=True)
    client_public_id = models.UUIDField(db_index=True)
    client_name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=PREFIX_LENGTH)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=500)
    route = models.CharField(max_length=500, blank=True)
    dry_run = models.BooleanField(default=False)
    status_code = models.PositiveSmallIntegerField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.key_prefix} {self.method} {self.path}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("ApiMutationLog is append-only")
        super().save(*args, **kwargs)


class ApiRateCounter(BaseModel):
    """The calls one API key made in its current rate limit window (CORE.12), per bucket.

    Kept in PostgreSQL, the only datastore (ADR-0001, spec 01: no Redis): every gunicorn worker
    and container counts on the same row, so the limit does not grow with the number of
    processes, as it would with a process-local cache. Django's database cache backend is not
    used because its `incr` is a read then a write, which loses counts under concurrency; the
    increment here is a single `INSERT … ON CONFLICT DO UPDATE` (`services.count_api_call`).

    One row per key and bucket, reused from window to window: the table never holds more rows
    than keys times limited scopes, so it needs no purge job. Rows go with their key.
    """

    vendor_scope: ClassVar[str] = "client__vendor"
    objects = VendorScopedManager()

    client = models.ForeignKey(ApiClient, on_delete=models.CASCADE, related_name="rate_counters")
    bucket = models.CharField(
        max_length=64,
        default=KEY_WIDE_BUCKET,
        help_text="The scope with a limit of its own, or '*' for the key-wide counter.",
    )
    window_start = models.DateTimeField()
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["client", "bucket"], name="api_rate_counter_unique")
        ]

    def __str__(self) -> str:
        return f"{self.client_id} {self.bucket}: {self.hits} since {self.window_start:%H:%M:%S}"
