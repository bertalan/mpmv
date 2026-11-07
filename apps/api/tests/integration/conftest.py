"""Shared helpers for ApiClient integration tests: keys are always generated at runtime."""

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone

from apps.api.models import ApiClient
from apps.api.services import issue_api_client
from apps.core.audit import AuditContext

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")

IssueFn = Callable[..., tuple[ApiClient, str]]


@pytest.fixture
def issue() -> IssueFn:
    """Issue a client with sensible defaults; returns (client, full key shown once)."""

    def _issue(**overrides: Any) -> tuple[ApiClient, str]:
        params: dict[str, Any] = {
            "name": "Catalog pipeline",
            "scopes": ["catalog:write", "stock:write"],
            "expires_at": timezone.now() + dt.timedelta(days=90),
            "allowed_ips": None,
        }
        params.update(overrides)
        issued = issue_api_client(**params, ctx=CTX)
        assert issued.client is not None
        assert issued.key is not None
        return issued.client, issued.key

    return _issue
