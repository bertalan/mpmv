"""Factories for the trust app (ARC.12)."""

import datetime as dt

import factory
from factory.django import DjangoModelFactory

from apps.trust import engine
from apps.trust.models import TrustRules, VendorTrustSnapshot
from apps.vendors.tests.factories import VendorFactory


class TrustRulesFactory(DjangoModelFactory[TrustRules]):
    """The spec default thresholds; consecutive rows are closed one-year periods."""

    class Meta:
        model = TrustRules

    version = factory.Sequence(lambda n: n + 1)
    params = factory.LazyFunction(lambda: engine.rules_to_params(engine.TrustRulesData(version=1)))
    valid_from = factory.Sequence(lambda n: dt.datetime(2026 + n, 1, 1, tzinfo=dt.UTC))
    valid_to = factory.LazyAttribute(lambda o: o.valid_from.replace(year=o.valid_from.year + 1))


class VendorTrustSnapshotFactory(DjangoModelFactory[VendorTrustSnapshot]):
    class Meta:
        model = VendorTrustSnapshot

    vendor = factory.SubFactory(VendorFactory)
    rules = factory.SubFactory(TrustRulesFactory)
    rules_version = factory.SelfAttribute("rules.version")
    as_of = factory.LazyFunction(lambda: dt.datetime(2027, 3, 2, 12, 0, tzinfo=dt.UTC))
    metrics = factory.LazyFunction(dict)
    badges = factory.LazyFunction(list)
