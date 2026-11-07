"""Core models: the abstract base model and the append-only audit log."""

import uuid
from typing import Any

from django.db import models


class BaseModel(models.Model):
    """Base for domain models: bigint PK (default), UUID `public_id` for APIs, timestamps."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Who changed what, before/after, and from where. Append-only (DB trigger + save guard).

    The actor is stored by id and label, not as a foreign key: audit rows must outlive users
    and must not cascade on deletion.
    """

    class Source(models.TextChoices):
        ADMIN = "admin"
        VENDOR = "vendor"
        API = "api"
        COMMAND = "command"
        TASK = "task"
        MCP = "mcp"
        SYSTEM = "system"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=150)
    source = models.CharField(max_length=16, choices=Source.choices)
    action = models.CharField(max_length=100, db_index=True)
    target_model = models.CharField(max_length=100)
    target_key = models.CharField(max_length=200)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [models.Index(fields=["target_model", "target_key"])]
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return (
            f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action} {self.target_key}"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("AuditLog is append-only")
        super().save(*args, **kwargs)
