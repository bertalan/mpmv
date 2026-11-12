"""factory_boy factories for the ledger (VEN.4). Synthetic values only.

`LedgerEntryFactory` makes a 10.00 EUR sale credit for a new vendor under the open-ended platform
operator row (made if missing), with a distinct idempotency key. It goes through
`services.record_entry`, the only writer, so the running balance and the audit entry are real.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import factory
from factory.django import DjangoModelFactory

from apps.core.audit import AuditContext
from apps.core.models import PlatformOperator
from apps.core.tests.factories import PlatformOperatorFactory
from apps.ledger import services
from apps.ledger.models import LedgerEntry
from apps.vendors.tests.factories import VendorFactory


def _open_operator() -> PlatformOperator:
    """The open-ended operator row, made once (rows of one period may not overlap)."""
    return PlatformOperator.objects.filter(valid_to__isnull=True).first() or (
        PlatformOperatorFactory.create(
            valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), open_ended=True
        )
    )


class LedgerEntryFactory(DjangoModelFactory[LedgerEntry]):
    class Meta:
        model = LedgerEntry
        skip_postgeneration_save = True

    vendor = factory.SubFactory(VendorFactory)
    kind = "sale"
    amount = Decimal("10.00")
    idempotency_key = factory.Sequence(lambda n: f"test-entry-{n}")
    platform_entity = None  # the open-ended operator row when created, an unsaved one when built

    @classmethod
    def _build(cls, model_class: type[LedgerEntry], *args: Any, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("platform_entity") is None:
            kwargs["platform_entity"] = PlatformOperatorFactory.build()
        return model_class(*args, **kwargs)

    @classmethod
    def _create(cls, model_class: type[LedgerEntry], *args: Any, **kwargs: Any) -> LedgerEntry:
        ctx = AuditContext(actor_label="system:test", source="system")
        kwargs["platform_entity"] = kwargs["platform_entity"] or _open_operator()
        services.record_entry(
            kwargs["vendor"],
            kind=kwargs["kind"],
            amount=kwargs["amount"],
            idempotency_key=kwargs["idempotency_key"],
            ctx=ctx,
            platform_entity=kwargs["platform_entity"],
            vendor_order=kwargs.get("vendor_order"),
        )
        return LedgerEntry.objects.get(idempotency_key=kwargs["idempotency_key"])
