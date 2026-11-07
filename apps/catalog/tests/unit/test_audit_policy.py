"""CAT.12: products and variants are audited models, with no price-only exemption.

Their admins write through the audited catalog services, so the SEC.4 system check
(`core.E203`) passes with both models in `audited_models`.
"""

from typing import Any

from django.apps import apps
from django.contrib import admin
from django.db import models

from apps.catalog.admin import ProductAdmin, VariantAdmin
from apps.catalog.models import Product, Variant
from apps.core.checks import audit_policy, audit_policy_errors


def test_products_and_variants_are_audited_without_an_exemption() -> None:
    policy = audit_policy([apps.get_app_config("catalog")])
    assert policy["catalog.product"] is None
    assert policy["catalog.variant"] is None


def test_the_catalog_admins_pass_the_audit_policy_check() -> None:
    policy = audit_policy(apps.get_app_configs())
    registry: dict[type[models.Model], admin.ModelAdmin[Any]] = {
        Product: admin.site._registry[Product],
        Variant: admin.site._registry[Variant],
    }
    assert isinstance(registry[Product], ProductAdmin)
    assert isinstance(registry[Variant], VariantAdmin)
    assert audit_policy_errors([Product, Variant], registry, policy) == []
