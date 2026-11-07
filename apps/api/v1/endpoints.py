"""API v1 endpoints. No business endpoints yet: those arrive with their own tasks."""

import datetime as dt
import uuid

from django.http import HttpRequest
from ninja import Router, Schema

from apps.api.auth import api_client_of

from .security import ApiKey

router = Router(tags=["clients"])


class MeOut(Schema):
    public_id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: dt.datetime


@router.get("/me", response=MeOut, auth=ApiKey(None), summary="The calling API client")
def me(request: HttpRequest) -> MeOut:
    """The client that owns the API key, with its scopes and expiry. Any valid key may call it."""
    client = api_client_of(request)
    return MeOut(
        public_id=client.public_id,
        name=client.name,
        key_prefix=client.key_prefix,
        scopes=sorted(client.scopes),
        expires_at=client.expires_at,
    )
