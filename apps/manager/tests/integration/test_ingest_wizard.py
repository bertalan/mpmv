"""ING.13 slice 1: the supplier-source checklist and step 1 of the "new vendor from source"
wizard. Staff-only (Catalogue writes; Support and the owner read), CSRF and the session gate on
the POST, the sample is parsed in memory and never stored, every write is audited. Language
agnostic: no UI text is matched. Pseudonymised data only. Ingest and access code: needs human
review."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import onboarding, services
from apps.ingest.adapters import base
from apps.ingest.models import MappingTable, SupplierSource
from apps.ingest.tests.factories import SupplierSourceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

NEW = "/manager/platform/sources/new/"
LIST = "/manager/platform/sources/"
CSV = b"Code;Name;Price\nA-1;Widget;1.250,50\nA-2;Gadget;9,90\n"


def staff(role: str) -> Client:
    user: User = UserFactory.create(platform_role=role)
    client = Client()
    verified_login(client, user)
    return client


def post(client: Client, vendor: Vendor, action: str, data: bytes = CSV, **extra: str):  # type: ignore[no-untyped-def]
    body = {
        "vendor": vendor.pk,
        "code": "price-list",
        "adapter_code": "csv_generic",
        "decimal": ",",
        "action": action,
        "sample": SimpleUploadedFile("sample.csv", data),
        **extra,
    }
    return client.post(NEW, body)


def test_a_check_reads_the_sample_and_writes_nothing() -> None:
    vendor = VendorFactory.create()
    response = post(staff("catalog"), vendor, "check")
    assert response.status_code == 200
    assert not SupplierSource.objects.exists()
    assert not AuditLog.objects.filter(action=services.SOURCE_CREATED).exists()
    assert "Code" in response.content.decode()  # the header found, so the mapping can follow


def test_create_saves_an_inactive_source_and_the_counts_not_the_file() -> None:
    vendor = VendorFactory.create()
    response = post(staff("catalog"), vendor, "create")
    assert response.status_code == 302
    source = SupplierSource.objects.get(vendor=vendor, code="price-list")
    assert not source.is_active
    assert not source.write_enabled
    assert source.config["options"] == {"decimal": ","}
    assert source.config["sample"]["ok"] is False  # no column is mapped yet
    assert "Widget" not in str(source.config)
    assert AuditLog.objects.filter(action=services.SOURCE_CREATED).exists()


def test_a_mapped_clean_sample_turns_the_checklist_green() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    for key, field in (("Code", "sku"), ("Name", "title"), ("Price", "price")):
        MappingTable.objects.create(
            vendor=vendor, kind="other", key=key, key_norm=key.lower(), target_value=field
        )
    source = SupplierSourceFactory.create(
        vendor=vendor, config={"options": {"decimal": ","}}, is_active=False
    )
    report = onboarding.run_sample(source, CSV)
    assert report.clean
    assert report.rows == 2
    items = {i.key: i.ok for i in onboarding.checklist(source)}
    assert items["mapping_complete"]
    assert not items["sample_clean"]
    onboarding.record_sample(
        source, report, ctx=AuditContext(actor_label="t", source=AuditLog.Source.SYSTEM)
    )
    items = {i.key: i.ok for i in onboarding.checklist(source)}
    assert items["vendor_active"]
    assert items["sample_clean"]
    assert not items["source_active"]


def test_a_changed_mapping_makes_the_sample_stale() -> None:
    vendor = VendorFactory.create()
    source = SupplierSourceFactory.create(vendor=vendor)
    report = onboarding.SampleReport(rows=1, mapping="old")
    source.config = {"sample": {"ok": True, "mapping": report.mapping}}
    assert not {i.key: i.ok for i in onboarding.checklist(source)}["sample_clean"]


def test_an_oversized_sample_is_refused_and_nothing_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "DEFAULT_MAX_BYTES", 10)
    vendor = VendorFactory.create()
    response = post(staff("catalog"), vendor, "create")
    assert response.status_code == 400
    assert not SupplierSource.objects.exists()


def test_a_duplicate_name_and_an_unknown_adapter_are_refused() -> None:
    vendor = VendorFactory.create()
    SupplierSourceFactory.create(vendor=vendor, code="price-list")
    client = staff("catalog")
    assert post(client, vendor, "create").status_code == 400
    assert post(client, vendor, "create", code="other", adapter_code="nope").status_code == 400
    assert SupplierSource.objects.count() == 1


def test_support_and_the_owner_read_but_cannot_write() -> None:
    vendor = VendorFactory.create()
    source = SupplierSourceFactory.create(vendor=vendor)
    for role in ("support", "owner"):
        client = staff(role)
        assert client.get(LIST).status_code == 200
        assert client.get(f"{LIST}{source.public_id}/").status_code == 200
        assert post(client, vendor, "create").status_code == 403
    assert SupplierSource.objects.count() == 1


@pytest.mark.parametrize("role", ["finance", "config"])
def test_other_platform_roles_get_404(role: str) -> None:
    client = staff(role)
    assert client.get(LIST).status_code == 404
    assert post(client, VendorFactory.create(), "create").status_code == 404


def test_a_vendor_member_cannot_reach_the_pages() -> None:
    membership = VendorMemberFactory.create(role="owner")
    client = Client()
    verified_login(client, membership.user)
    assert client.get(LIST).status_code in (403, 404)
    assert post(client, membership.vendor, "create").status_code in (403, 404)
    assert not SupplierSource.objects.exists()


def test_the_post_needs_the_csrf_token() -> None:
    user = UserFactory.create(platform_role="catalog")
    client = Client(enforce_csrf_checks=True)
    verified_login(client, user)
    body = {"vendor": 1, "code": "x", "adapter_code": "csv_generic", "action": "create"}
    assert client.post(NEW, body).status_code == 403


def test_the_checklist_of_an_unknown_source_is_404() -> None:
    assert staff("catalog").get(f"{LIST}00000000-0000-0000-0000-000000000000/").status_code == 404


def test_an_empty_file_is_reported_not_raised() -> None:
    source = SupplierSourceFactory.create()
    report = onboarding.run_sample(source, io.BytesIO(b"").read())
    assert report.failure
    assert not report.clean


def test_a_failed_sample_record_leaves_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(onboarding, "record_sample", boom)
    client = staff("catalog")
    client.raise_request_exception = False
    assert post(client, VendorFactory.create(), "create").status_code == 500
    assert not SupplierSource.objects.exists()
    assert not AuditLog.objects.filter(action=services.SOURCE_CREATED).exists()


def test_a_long_column_name_is_cut_in_the_kept_headers() -> None:
    source = SupplierSourceFactory.create()
    report = onboarding.SampleReport(headers=["h" * 500])
    ctx = AuditContext(actor_label="t", source=AuditLog.Source.SYSTEM)
    onboarding.record_sample(source, report, ctx=ctx)
    assert len(source.config["sample"]["headers"][0]) == onboarding.MAX_HEADER_CHARS


def test_create_source_refusals_are_translated() -> None:
    """The refusal texts go through gettext (formal register in the catalogs)."""
    from django.core.exceptions import ValidationError
    from django.utils import translation

    vendor = VendorFactory.create()
    ctx = AuditContext(actor_label="t", source=AuditLog.Source.SYSTEM)
    with translation.override("it"), pytest.raises(ValidationError) as caught:
        onboarding.create_source(vendor, code="x", adapter_code="nope", ctx=ctx)
    assert "nope" not in str(caught.value)
    assert caught.value.message_dict["adapter_code"] != ["Choose one of the listed adapters."]


def test_a_checklist_item_has_a_label_not_only_a_key() -> None:
    source = SupplierSourceFactory.create(vendor=VendorFactory.create())
    for item in onboarding.checklist(source):
        assert item.label
        assert item.label != item.key
