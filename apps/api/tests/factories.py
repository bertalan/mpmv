"""factory_boy factories for the API app (ARC.12). Keys are generated at runtime, never stored."""

import datetime as dt
import uuid
from typing import Any

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.api.engine import KEY_WIDE_BUCKET, generate_key, hash_key
from apps.api.models import ApiClient, ApiMutationLog, ApiRateCounter, ApiRefusedKey
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory


class ApiClientFactory(DjangoModelFactory[ApiClient]):
    """An active platform client with a fresh random key, as `issue_api_client` would store it.

    Only the prefix and the hash are stored. Use `create_with_key()` when the test needs the full
    key; `expired=True` and `revoked=True` give refused clients; `vendor_key=True` binds the
    client to a new active vendor (or pass `vendor=`) (CORE.13). Tests about issuance itself call
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
        # An active vendor: only an active vendor's keys write (CORE.14).
        vendor_key = factory.Trait(
            vendor=factory.SubFactory(VendorFactory, status=Vendor.Status.ACTIVE)
        )

    vendor = None
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


class ApiMutationLogFactory(DjangoModelFactory[ApiMutationLog]):
    """A logged mutating call (CORE.7). Rows are append-only: build the values you assert on."""

    class Meta:
        model = ApiMutationLog

    request_id = factory.LazyFunction(lambda: uuid.uuid4().hex)
    client_public_id = factory.LazyFunction(uuid.uuid4)
    client_name = factory.Sequence(lambda n: f"Pipeline {n}")
    key_prefix = factory.LazyFunction(lambda: generate_key().prefix)
    method = "POST"
    path = "/api/v1/products"
    route = "api/v1/products"
    dry_run = False
    status_code = 201
    ip = "203.0.113.7"
    duration_ms = 12


class ApiRateCounterFactory(DjangoModelFactory[ApiRateCounter]):
    """The request counter of one key in its current window (CORE.12), key-wide by default."""

    class Meta:
        model = ApiRateCounter

    client = factory.SubFactory(ApiClientFactory)
    bucket = KEY_WIDE_BUCKET
    window_start = factory.LazyFunction(lambda: timezone.now().replace(second=0, microsecond=0))
    hits = 1


class ApiRefusedKeyFactory(DjangoModelFactory[ApiRefusedKey]):
    """One refused-key (401) answer to a client IP bucket, now (CORE.20)."""

    class Meta:
        model = ApiRefusedKey

    ip_bucket = "203.0.113.77"
    refused_at = factory.LazyFunction(timezone.now)
