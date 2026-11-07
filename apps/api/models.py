"""API keys for API v1 and MCP (CORE.6, spec 02 §2.2) and the API mutation log (CORE.7)."""

import uuid
from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import BaseModel

from .engine import PREFIX_LENGTH, ClientState


class ApiClient(BaseModel):
    """An API key holder: pipelines, MCP, integrations.

    Only the public prefix and the SHA-256 of the key are stored; the full key is shown once at
    issuance or rotation. The vendor link (`vendor`, null = platform client) arrives with the
    Vendor model (VEN.1).
    """

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

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.key_prefix})"

    def state(self) -> ClientState:
        return ClientState(
            is_active=self.is_active,
            expires_at=self.expires_at,
            scopes=tuple(self.scopes),
            allowed_ips=None if self.allowed_ips is None else tuple(self.allowed_ips),
        )

    def snapshot(self) -> dict[str, Any]:
        """Audit/ChangeSet view: everything but the key hash."""
        return {
            "name": self.name,
            "scopes": list(self.scopes),
            "key_prefix": self.key_prefix,
            "expires_at": self.expires_at,
            "allowed_ips": None if self.allowed_ips is None else list(self.allowed_ips),
            "is_active": self.is_active,
            "revoked_at": self.revoked_at,
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
