"""Factories for the payments app's models."""

from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.payments.models import Payment, ProcessedEvent


class ProcessedEventFactory(DjangoModelFactory[ProcessedEvent]):
    class Meta:
        model = ProcessedEvent

    provider = "stripe"
    event_id = factory.Sequence(lambda n: f"evt_factory_{n}")
    event_type = "payment_intent.succeeded"


class PaymentFactory(DjangoModelFactory[Payment]):
    class Meta:
        model = Payment

    provider = "stripe"
    provider_ref = factory.Sequence(lambda n: f"pi_factory_{n}")
    charge_ref = factory.Sequence(lambda n: f"ch_factory_{n}")
    order_ref = factory.Sequence(lambda n: f"order-factory-{n}")
    amount = Decimal("10.00")
    currency = "EUR"
    succeeded_at = factory.LazyFunction(timezone.now)
