"""ING.13 slice 2: header mapping, re-check of the sample and activation, and the Done-when: a
demo vendor goes from nothing to an active source through the pages only, no code change.
Language agnostic. Pseudonymised data. Ingest and access code: needs human review."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import onboarding, services
from apps.ingest.models import MappingTable, SupplierSource
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CSV = b"Code;Name;Price\nA-1;Widget;1.250,50\nA-2;Gadget;9,90\n"


def staff(role: str = "catalog") -> Client:
    client = Client()
    verified_login(client, UserFactory.create(platform_role=role))
    return client


def sample(data: bytes = CSV) -> SimpleUploadedFile:
    return SimpleUploadedFile("sample.csv", data)


def test_a_demo_vendor_is_activated_without_code() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    client = staff()
    # Step 1: source and sample (nothing is mapped yet).
    body = {
        "vendor": vendor.pk,
        "code": "demo",
        "adapter_code": "csv_generic",
        "decimal": ",",
        "action": "create",
        "sample": sample(),
    }
    response = client.post("/manager/platform/sources/new/", body)
    assert response.status_code == 302
    source = SupplierSource.objects.get(vendor=vendor, code="demo")
    base = f"/manager/platform/sources/{source.public_id}/"
    # Activation is refused while the checklist has gaps.
    assert client.post(base, {"action": "activate"}).status_code == 400
    source.refresh_from_db()
    assert not source.is_active
    # Step 2: read the headers from the sample, then save the mapping.
    page = client.post(f"{base}mapping/", {"action": "headers", "sample": sample()})
    assert page.status_code == 200
    assert b"Price" in page.content
    pairs = {"action": "save"}
    for i, (header, name) in enumerate((("Code", "sku"), ("Name", "title"), ("Price", "price"))):
        pairs |= {f"header_{i}": header, f"field_{i}": name}
    assert client.post(f"{base}mapping/", pairs).status_code == 302
    assert MappingTable.objects.filter(vendor=vendor, kind="other").count() == 3
    assert AuditLog.objects.filter(action=services.MAPPING_CREATED).count() == 3
    # Step 3: re-check the sample with the mapping, then activate.
    assert client.post(base, {"action": "check", "sample": sample()}).status_code == 302
    assert client.post(base, {"action": "activate"}).status_code == 302
    source.refresh_from_db()
    assert source.is_active
    assert not source.write_enabled  # writing stays a separate, later decision
    assert AuditLog.objects.filter(action=services.SOURCE_UPDATED).exists()
    assert all(i.ok for i in onboarding.checklist(source))


def test_a_changed_mapping_blocks_activation_until_the_sample_is_rechecked() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    source, _ = onboarding.create_source(
        vendor,
        code="demo",
        adapter_code="csv_generic",
        ctx=_ctx(),
    )
    onboarding.save_column_mappings(vendor, {"Code": "sku", "Name": "title"}, ctx=_ctx())
    onboarding.record_sample(source, onboarding.run_sample(source, CSV), ctx=_ctx())
    assert onboarding.checklist(source)[3].ok
    onboarding.save_column_mappings(vendor, {"Price": "price"}, ctx=_ctx())
    assert not onboarding.checklist(source)[3].ok


def test_mapping_is_validated_and_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = onboarding.save_column_mappings(vendor, {"Code": "sku"}, ctx=_ctx(), dry_run=True)
    assert changes.changes
    assert not MappingTable.objects.exists()
    with pytest.raises(Exception, match="field"):
        onboarding.save_column_mappings(vendor, {"Code": "nope"}, ctx=_ctx())
    assert not MappingTable.objects.exists()


def test_remapping_a_header_updates_the_row() -> None:
    vendor = VendorFactory.create()
    onboarding.save_column_mappings(vendor, {"Code": "sku"}, ctx=_ctx())
    onboarding.save_column_mappings(vendor, {"code": "ean"}, ctx=_ctx())
    row = MappingTable.objects.get(vendor=vendor)
    assert row.target_value == "ean"


def test_read_only_roles_cannot_map_or_activate() -> None:
    source = SupplierSourceFactoryCreate()
    base = f"/manager/platform/sources/{source.public_id}/"
    for role in ("support", "owner"):
        client = staff(role)
        assert client.get(f"{base}mapping/").status_code == 200
        assert client.post(f"{base}mapping/", {"action": "save"}).status_code == 403
        assert client.post(base, {"action": "activate"}).status_code == 403
    source.refresh_from_db()
    assert not source.is_active


def _ctx() -> AuditContext:
    return AuditContext(actor_label="t", source=AuditLog.Source.SYSTEM)


def SupplierSourceFactoryCreate() -> SupplierSource:
    from apps.ingest.tests.factories import SupplierSourceFactory

    return SupplierSourceFactory.create(is_active=False)
