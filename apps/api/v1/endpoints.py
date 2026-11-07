"""API v1 endpoints. No business endpoints yet: those arrive with their own tasks."""

import datetime as dt
import uuid

from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from apps.api.auth import api_client_of

from .security import ApiKey

router = Router(tags=["clients"])


class MeOut(Schema):
    public_id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: dt.datetime
    vendor: uuid.UUID | None = Field(
        description="Public id of the vendor a vendor key acts for; null for a platform key."
    )


@router.get("/me", response=MeOut, auth=ApiKey(None), summary="The calling API client")
def me(request: HttpRequest) -> MeOut:
    """The client that owns the API key, with its scopes, expiry and vendor. Any valid key may call
    it."""
    client = api_client_of(request)
    return MeOut(
        vendor=None if client.vendor is None else client.vendor.public_id,
        public_id=client.public_id,
        name=client.name,
        key_prefix=client.key_prefix,
        scopes=sorted(client.scopes),
        expires_at=client.expires_at,
    )
