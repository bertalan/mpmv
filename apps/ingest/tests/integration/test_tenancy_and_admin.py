"""ING.1: staging data is vendor-scoped (CORE.3) and the admin is staff only."""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.ingest.models import (
    ImportRun,
    MappingTable,
    ProductDraft,
    ReviewItem,
    SourceItem,
    SourceObservation,
    SupplierSource,
)
from apps.ingest.tests.factories import (
    ImportRunFactory,
    MappingTableFactory,
    ProductDraftFactory,
    ReviewItemFactory,
    SourceItemFactory,
    SourceObservationFactory,
    SupplierSourceFactory,
)
from apps.vendors.models import Vendor
from tests.tenancy import CrossTenant

pytestmark = pytest.mark.django_db

MODELS = (
    SupplierSource, SourceItem, SourceObservation, ProductDraft, ImportRun, ReviewItem,
    MappingTable,
)  # fmt: skip


def test_vendor_a_cannot_see_vendor_bs_staging_data(cross_tenant: CrossTenant) -> None:
    a, b = cross_tenant.a, cross_tenant.b
    makers: dict[type[Any], Callable[[Vendor], Any]] = {
        SupplierSource: lambda v: SupplierSourceFactory.create(vendor=v),
        SourceItem: lambda v: SourceItemFactory.create(source__vendor=v),
        SourceObservation: lambda v: SourceObservationFactory.create(item__source__vendor=v),
        ProductDraft: lambda v: ProductDraftFactory.create(item__source__vendor=v),
        ImportRun: lambda v: ImportRunFactory.create(source__vendor=v),
        ReviewItem: lambda v: ReviewItemFactory.create(run__source__vendor=v),
        MappingTable: lambda v: MappingTableFactory.create(vendor=v),
    }
    for model in MODELS:
        mine = makers[model](a.vendor)
        theirs = makers[model](b.vendor)
        visible = model.objects.for_member(a.owner)
        assert mine in visible, model
        assert theirs not in visible, model


def test_platform_mappings_are_visible_to_staff_only(cross_tenant: CrossTenant) -> None:
    from apps.accounts.tests.factories import UserFactory

    shared = MappingTableFactory.create(vendor=None)
    assert shared not in MappingTable.objects.for_member(cross_tenant.a.owner)
    assert shared in MappingTable.objects.for_actor(UserFactory.create(platform_staff=True))


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


@pytest.mark.parametrize("model", MODELS)
def test_every_ingest_model_is_in_the_staff_admin(staff_client: Client, model: type[Any]) -> None:
    url = reverse(f"admin:ingest_{model._meta.model_name}_changelist")
    assert staff_client.get(url).status_code == 200


@pytest.mark.parametrize("model", MODELS)
def test_a_vendor_member_cannot_open_the_ingest_admin(
    cross_tenant: CrossTenant,
    client: Client,
    mfa_login: Callable[[Client, User], Client],
    model: type[Any],
) -> None:
    client = mfa_login(client, cross_tenant.a.owner)
    url = reverse(f"admin:ingest_{model._meta.model_name}_changelist")
    assert client.get(url).status_code in (302, 403, 404)


def test_a_mapping_added_in_the_admin_is_audited(staff_client: Client) -> None:
    from apps.vendors.tests.factories import VendorFactory

    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:ingest_mappingtable_add"),
        {
            "vendor": vendor.pk, "kind": "color", "key": "Rosso", "target_value": "red",
            "aliases": "[]",
        },
    )  # fmt: skip
    assert response.status_code == 302, response.content[:500]
    assert MappingTable.objects.get().key_norm == "rosso"
    assert AuditLog.objects.filter(action="ingest.mapping.created").count() == 1


def test_staging_rows_and_runs_are_read_only_in_the_admin(staff_client: Client) -> None:
    run = ImportRunFactory.create()
    assert staff_client.get(reverse("admin:ingest_importrun_add")).status_code == 403
    response = staff_client.post(
        reverse("admin:ingest_importrun_change", args=[run.pk]), {"status": "failed"}
    )
    assert response.status_code in (200, 403)
    run.refresh_from_db()
    assert run.status == "queued"
