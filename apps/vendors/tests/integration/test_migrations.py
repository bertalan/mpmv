"""I18N.2: moving VendorTranslation onto TranslationModel keeps every existing row and text.

Rows written before the translation workflow existed were live, so they become `published`;
their `source_hash` is filled so that none of them starts out stale.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core.translations import PUBLISHED, source_hash
from apps.vendors.models import VendorTranslation

BEFORE = [("vendors", "0001_initial")]

TEXTS = ("tagline", "description", "shipping_policy", "refund_policy", "cancellation_policy")


@pytest.mark.django_db(transaction=True)
def test_existing_translations_are_kept_published_and_not_stale() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old_apps = executor.loader.project_state(BEFORE).apps
    OldVendor = old_apps.get_model("vendors", "Vendor")
    OldTranslation = old_apps.get_model("vendors", "VendorTranslation")

    vendor_a = OldVendor.objects.create(
        legal_name="Vendor A S.r.l.",
        display_name="Vendor A",
        slug="vendor-a",
        country="IT",
        contact_email="vendor-a@example.com",
    )
    vendor_b = OldVendor.objects.create(
        legal_name="Vendor B S.r.l.",
        display_name="Vendor B",
        slug="vendor-b",
        country="IT",
        contact_email="vendor-b@example.com",
    )
    en_texts = {
        "tagline": "Outdoor gear",
        "description": "Family business.",
        "shipping_policy": "Ships in 2 days.",
        "refund_policy": "Refunds within 14 days.",
        "cancellation_policy": "Cancel before shipping.",
    }
    OldTranslation.objects.create(vendor=vendor_a, language="en", **en_texts)
    OldTranslation.objects.create(vendor=vendor_a, language="it", tagline="Attrezzatura")
    OldTranslation.objects.create(vendor=vendor_b, language="de", tagline="Ausrüstung")

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    rows = {
        (row.vendor.slug, row.language): row
        for row in VendorTranslation.objects.select_related("vendor")
    }
    assert set(rows) == {("vendor-a", "en"), ("vendor-a", "it"), ("vendor-b", "de")}
    en = rows["vendor-a", "en"]
    assert {name: getattr(en, name) for name in TEXTS} == en_texts
    assert rows["vendor-a", "it"].tagline == "Attrezzatura"
    assert rows["vendor-b", "de"].tagline == "Ausrüstung"

    assert {row.status for row in rows.values()} == {PUBLISHED}
    assert en.source_hash == source_hash(en_texts)
    assert rows["vendor-a", "it"].source_hash == en.source_hash
    assert rows["vendor-b", "de"].source_hash == ""  # no EN source to compare with
    assert not any(row.is_stale for row in rows.values())
