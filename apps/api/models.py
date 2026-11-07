"""Machine credentials for API v1 and MCP (CORE.6, spec 02 §2.2)."""

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
