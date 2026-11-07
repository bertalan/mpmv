"""factory_boy factories for the API app (ARC.12). Keys are generated at runtime, never stored."""

import datetime as dt
from typing import Any

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.api.engine import generate_key, hash_key
from apps.api.models import ApiClient


class ApiClientFactory(DjangoModelFactory[ApiClient]):
    """An active platform client with a fresh random key, as `issue_api_client` would store it.

    Only the prefix and the hash are stored. Use `create_with_key()` when the test needs the full
    key; `expired=True` and `revoked=True` give refused clients. Tests about issuance itself call
    the service, not this factory.
    """

    class Meta:
        model = ApiClient

    class Params:
        generated = factory.LazyFunction(generate_key)
        expired = factory.Trait(
            expires_at=factory.LazyFunction(lambda: timezone.now() - dt.timedelta(minutes=1))
        )
        revoked = factory.Trait(is_active=False, revoked_at=factory.LazyFunction(timezone.now))

    name = factory.Sequence(lambda n: f"Pipeline {n}")
    scopes = factory.LazyFunction(lambda: ["catalog:write", "stock:write"])
    key_prefix = factory.SelfAttribute("generated.prefix")
    key_hash = factory.LazyAttribute(lambda o: hash_key(o.generated.key))
    expires_at = factory.LazyFunction(lambda: timezone.now() + dt.timedelta(days=90))
    allowed_ips = None
    is_active = True

    @classmethod
    def create_with_key(cls, **kwargs: Any) -> tuple[ApiClient, str]:
        """Create a client and return it with its full key (the only time the key exists)."""
        generated = generate_key()
        return cls.create(generated=generated, **kwargs), generated.key
