"""CAT.20 (2/3): staff review brand proposals in the admin (approve, reject, merge), and the
admin writes of brands and manufacturers are audited."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.catalog import brands
from apps.catalog.models import Brand, BrandBatch, Manufacturer, Product
from apps.catalog.tests.factories import BrandFactory, ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
CHANGELIST = "admin:catalog_brand_changelist"


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def _pending(name: str = "Acme") -> Brand:
    brand = brands.propose_brand(VendorFactory.create(), name, ctx=CTX).brand
    assert brand is not None
    return brand


def test_the_approve_action_approves_the_selected_proposals(staff_client: Client) -> None:
    first, second = _pending("One"), _pending("Two")
    response = staff_client.post(
        reverse(CHANGELIST),
        {"action": "approve_selected", "_selected_action": [first.pk, second.pk]},
        follow=True,
    )
    assert response.status_code == 200
    assert Brand.objects.filter(status=Brand.Status.APPROVED).count() == 2
    assert AuditLog.objects.filter(action=brands.BRAND_APPROVED).count() == 2


def test_the_reject_action_rejects_the_selected_proposals(staff_client: Client) -> None:
    pending = _pending()
    staff_client.post(
        reverse(CHANGELIST),
        {"action": "reject_selected", "_selected_action": [pending.pk]},
        follow=True,
    )
    pending.refresh_from_db()
    assert pending.status == Brand.Status.REJECTED


def test_the_merge_action_asks_for_the_target_then_merges(staff_client: Client) -> None:
    source, target = _pending("Acmee"), BrandFactory.create(name="Acme")
    ProductFactory.create(brand=source, vendor=source.proposed_by)
    ask = staff_client.post(
        reverse(CHANGELIST),
        {"action": "merge_selected", "_selected_action": [source.pk]},
    )
    assert ask.status_code == 200
    assert b'name="target"' in ask.content
    done = staff_client.post(
        reverse(CHANGELIST),
        {
            "action": "merge_selected",
            "_selected_action": [source.pk],
            "target": target.pk,
            "confirm": "1",
        },
        follow=True,
    )
    assert done.status_code == 200
    assert not Brand.objects.filter(pk=source.pk).exists()
    assert Product.objects.filter(brand=target).count() == 1


def test_a_staff_edit_of_a_brand_is_audited(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_brand_add"),
        {
            "name": "Acme",
            "wikidata_id": "",
            "url": "",
            "same_as": "[]",
            "logo_alt": "",
            "translations-TOTAL_FORMS": "0",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
        },
    )
    assert response.status_code == 302
    brand = Brand.objects.get(name="Acme")
    assert brand.status == Brand.Status.APPROVED
    assert AuditLog.objects.filter(action=brands.BRAND_CREATED).count() == 1


def test_a_manufacturer_admin_save_is_audited(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_manufacturer_add"),
        {"legal_name": "Maker Ltd", "url": "", "same_as": "[]", "country": ""},
    )
    assert response.status_code == 302
    assert Manufacturer.objects.filter(legal_name="Maker Ltd").exists()
    assert AuditLog.objects.filter(action=brands.MANUFACTURER_CREATED).count() == 1


def test_a_batch_is_reviewed_in_the_admin(staff_client: Client) -> None:
    outcome = brands.propose_batch(VendorFactory.create(), ["A", "B"], source="run", ctx=CTX)
    batch = outcome.batch
    assert batch is not None
    staff_client.post(
        reverse("admin:catalog_brandbatch_changelist"),
        {"action": "approve_selected", "_selected_action": [batch.pk]},
        follow=True,
    )
    assert Brand.objects.filter(status=Brand.Status.APPROVED).count() == 2
    assert BrandBatch.objects.filter(pk=batch.pk).exists()


@pytest.fixture
def change_only_client(client: Client, mfa_login: MfaLogin) -> Client:
    user = UserFactory.create(
        staff=True,
        permissions=["catalog.view_brand", "catalog.change_brand", "catalog.view_brandbatch"],
    )
    return mfa_login(client, user)


def test_a_change_only_user_reviews_but_cannot_merge(change_only_client: Client) -> None:
    first, second = _pending("Acmee"), _pending("Two")
    target = BrandFactory.create(name="Acme")
    change_only_client.post(
        reverse(CHANGELIST),
        {
            "action": "merge_selected",
            "_selected_action": [first.pk],
            "target": target.pk,
            "confirm": "1",
        },
        follow=True,
    )
    assert Brand.objects.filter(pk=first.pk).exists()
    change_only_client.post(
        reverse(CHANGELIST),
        {"action": "approve_selected", "_selected_action": [second.pk]},
        follow=True,
    )
    second.refresh_from_db()
    assert second.status == Brand.Status.APPROVED


def test_a_change_only_user_can_review_a_batch(change_only_client: Client) -> None:
    batch = brands.propose_batch(VendorFactory.create(), ["A"], source="run", ctx=CTX).batch
    assert batch is not None
    change_only_client.post(
        reverse("admin:catalog_brandbatch_changelist"),
        {"action": "approve_selected", "_selected_action": [batch.pk]},
        follow=True,
    )
    assert Brand.objects.get(name="A").status == Brand.Status.APPROVED
