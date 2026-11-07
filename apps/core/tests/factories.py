"""factory_boy factories for core models (ARC.12). Synthetic values only."""

import datetime as dt

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.core import flags
from apps.core.models import AuditLog, FeatureFlag, Heartbeat, PlatformOperator, Throttle


def utc(year: int, month: int = 1, day: int = 1, hour: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, tzinfo=dt.UTC)


def _one_year_later(start: dt.datetime) -> dt.datetime:
    try:
        return start.replace(year=start.year + 1)
    except ValueError:  # 29 February
        return start.replace(year=start.year + 1, day=28)


class HeartbeatFactory(DjangoModelFactory[Heartbeat]):
    class Meta:
        model = Heartbeat

    name = factory.Sequence(lambda n: f"test.job.{n}")
    beats = 1
    last_beat_at = factory.LazyFunction(lambda: timezone.now() - dt.timedelta(minutes=5))
    scheduled_for = None


class AuditLogFactory(DjangoModelFactory[AuditLog]):
    """An audit row as `apps.core.audit.record` writes it.

    Services write the real ones; use this only for tests about the log itself (append-only
    guards, listings). Rows are inserted once and never updated.
    """

    class Meta:
        model = AuditLog

    actor_id = factory.Sequence(lambda n: n + 1)
    actor_label = factory.LazyAttribute(lambda o: f"staff:user{o.actor_id}@example.com")
    source = AuditLog.Source.ADMIN
    action = "test.record.updated"
    target_model = "test.record"
    target_key = factory.Sequence(lambda n: f"record-{n}")
    before = factory.LazyFunction(lambda: {"value": 1})
    after = factory.LazyFunction(lambda: {"value": 2})
    ip = "127.0.0.1"


class PlatformOperatorFactory(DjangoModelFactory[PlatformOperator]):
    """A closed one-year configuration period; consecutive rows are adjacent, never overlapping.

    `open_ended=True` gives the current, open-ended row. Pass explicit dates when a test is
    about validity periods. For `configure_platform_operator`, use `.build()`: the service saves.
    """

    class Meta:
        model = PlatformOperator

    class Params:
        open_ended = factory.Trait(valid_to=None)

    legal_name = factory.Faker("company")
    country = "AT"
    vat_number = factory.Sequence(lambda n: f"ATU{n:08d}")
    tax_regime = PlatformOperator.TaxRegime.SMALL_BUSINESS
    commission_vat_mode = PlatformOperator.CommissionVatMode.NONE
    einvoice_channel = PlatformOperator.EInvoiceChannel.NONE
    dac7_authority_country = "AT"
    valid_from = factory.Sequence(lambda n: utc(2027 + n))
    valid_to = factory.LazyAttribute(lambda o: _one_year_later(o.valid_from))


class ThrottleFactory(DjangoModelFactory[Throttle]):
    """A throttle counter below any lock, forgotten in a day (CORE.5).

    Services write the real ones through `record_throttle_failure`; pass `failures`,
    `locked_until` and `expires_at` explicitly when a test is about locking.
    """

    class Meta:
        model = Throttle

    key = factory.Sequence(lambda n: f"test:throttle:{n}")
    failures = 1
    locked_until = None
    expires_at = factory.LazyFunction(lambda: timezone.now() + dt.timedelta(days=1))


def _registered_flag(n: int) -> str:
    names = [spec.name for spec in flags.registered_flags()]
    return names[n % len(names)]


class FeatureFlagFactory(DjangoModelFactory[FeatureFlag]):
    """The staff switch of a registered flag, at its environment default (ARC.8).

    Names cycle through the registered flags, and rows are looked up by name: the kill switch
    rows exist from the first migrate, so `FeatureFlagFactory()` returns one of them. For a flag
    of your own, register it with `apps.core.tests.flags.registered` and pass its name. Services
    validate names; use `.build()` with `save_flag`.
    """

    class Meta:
        model = FeatureFlag
        django_get_or_create = ("name",)

    name = factory.Sequence(_registered_flag)
    state = FeatureFlag.State.DEFAULT
    note = ""
