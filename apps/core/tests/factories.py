"""factory_boy factories for core models (ARC.12). Synthetic values only."""

import datetime as dt
from typing import Any

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory
from wagtail.models import Collection

from apps.core import flags
from apps.core.models import (
    AuditLog,
    EmailSuppression,
    FeatureFlag,
    GeocodeCacheEntry,
    GeocoderState,
    Heartbeat,
    ImageLicence,
    MediaImage,
    MediaImageRendition,
    OutboxEmail,
    OutboxEmailEvent,
    PlatformOperator,
    Throttle,
)


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


class ImageLicenceFactory(DjangoModelFactory[ImageLicence]):
    """An allow-list entry (CMS.8), looked up by code: the seeded rows are reused. Services
    validate entries; use `.build()` with `save_image_licence`."""

    class Meta:
        model = ImageLicence
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"licence-{n}")
    name = factory.LazyAttribute(lambda o: o.code)
    url = ""
    attribution_required = False
    source_required = True
    permission_required = False


class GeocodeCacheEntryFactory(DjangoModelFactory[GeocodeCacheEntry]):
    """A cached lookup with one candidate, valid for 180 days (EVT.12).

    The chain writes the real ones through `apps.core.geocoding.services.geocode_address`.
    """

    class Meta:
        model = GeocodeCacheEntry

    key = factory.Sequence(lambda n: f"{n:064x}")
    language = "en"
    normalised_address = factory.Sequence(lambda n: f"it|20121|milano|via roma {n}")
    provider = "nominatim"
    candidates = factory.LazyFunction(
        lambda: [
            {
                "latitude": "45.464270",
                "longitude": "9.189510",
                "label": "Milano",
                "precision": "locality",
                "confidence": None,
                "provider": "nominatim",
            }
        ]
    )
    expires_at = factory.LazyFunction(lambda: timezone.now() + dt.timedelta(days=180))


class GeocoderStateFactory(DjangoModelFactory[GeocoderState]):
    """A provider with a closed circuit and no reserved slot (EVT.12)."""

    class Meta:
        model = GeocoderState

    provider = factory.Sequence(lambda n: f"provider{n}")
    next_slot_at = None
    failures = 0
    open_until = None


class MediaImageFactory(DjangoModelFactory[MediaImage]):
    """A small generated PNG (I18N.3). Pass `file__filename` to recognise it in rendered HTML.

    It is the operator's own work by a test author (CMS.8), so it needs no source or credit;
    pass `licence=None, author=""` for a migrated image still to classify.

    Saving writes the file to MEDIA_ROOT, a temporary directory under the test settings. The
    image goes into Wagtail's root collection; `.build()` leaves the collection empty, because
    Wagtail's default looks it up in the database.
    """

    class Meta:
        model = MediaImage

    title = factory.Sequence(lambda n: f"Image {n}")
    file = factory.django.ImageField(width=64, height=48, color="teal", filename="image.png")
    collection = None
    licence = factory.SubFactory(
        ImageLicenceFactory, code="own-work", name="Own work", source_required=False
    )
    author = "Test author"

    @classmethod
    def _create(cls, model_class: type[MediaImage], *args: Any, **kwargs: Any) -> MediaImage:
        if kwargs.get("collection") is None:
            # A transactional test flushes the root collection its migration created.
            root = Collection.get_first_root_node() or Collection.add_root(name="Root")
            kwargs["collection"] = root
        image: MediaImage = model_class._default_manager.create(*args, **kwargs)
        return image


class MediaImageRenditionFactory(DjangoModelFactory[MediaImageRendition]):
    """A rendition as Wagtail's `get_rendition` stores it; templates normally create these."""

    class Meta:
        model = MediaImageRendition

    image = factory.SubFactory(MediaImageFactory)
    filter_spec = factory.Sequence(lambda n: f"width-{n + 10}")
    focal_point_key = ""
    file = factory.django.ImageField(width=10, height=8, color="teal", filename="rendition.png")


class OutboxEmailFactory(DjangoModelFactory[OutboxEmail]):
    """A queued transactional email (ARC.16) for a test template.

    Services write the real ones through `enqueue_email`, which also queues the send job; use
    this for tests about stored rows (history, admin listings).
    """

    class Meta:
        model = OutboxEmail

    recipient = factory.Sequence(lambda n: f"recipient{n}@example.com")
    template_key = "tests.one_time_code"
    language = "en"
    context = factory.LazyFunction(lambda: '{"code": "000000", "minutes": 10}')
    status = OutboxEmail.Status.QUEUED


class OutboxEmailEventFactory(DjangoModelFactory[OutboxEmailEvent]):
    """One append-only history row of an outbox email."""

    class Meta:
        model = OutboxEmailEvent

    email = factory.SubFactory(OutboxEmailFactory)
    status = OutboxEmailEvent.Kind.QUEUED
    attempt = 0
    detail = ""


class EmailSuppressionFactory(DjangoModelFactory[EmailSuppression]):
    """An address that receives no more email after a hard bounce (ARC.16)."""

    class Meta:
        model = EmailSuppression

    address = factory.Sequence(lambda n: f"bounced{n}@example.com")
    reason = EmailSuppression.Reason.HARD_BOUNCE
    detail = ""
