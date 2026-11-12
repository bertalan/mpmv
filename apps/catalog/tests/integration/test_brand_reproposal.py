"""CAT.20 (2/3): a rejected brand can be proposed again (the rejection notice says so), and the
admin change form cannot move a brand's status around the review workflow."""

from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog import brands
from apps.catalog.models import Brand
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
MfaLogin = Callable[[Client, User], Client]


def _rejected(name: str = "Acme", wikidata_id: str = "") -> Brand:
    brand = brands.propose_brand(
        VendorFactory.create(), name, ctx=CTX, wikidata_id=wikidata_id
    ).brand
    assert brand is not None
    brands.reject_brand(brand, ctx=CTX)
    brand.refresh_from_db()
    assert brand.status == Brand.Status.REJECTED
    return brand


def test_a_rejected_name_can_be_proposed_again_by_another_vendor() -> None:
    rejected = _rejected("Acme")
    vendor = VendorFactory.create()
    outcome = brands.propose_brand(vendor, "acme", ctx=CTX, url="https://acme.example/about")
    assert outcome.brand is not None
    assert outcome.brand.pk == rejected.pk
    assert outcome.brand.status == Brand.Status.PENDING
    assert outcome.brand.proposed_by == vendor
    assert Brand.objects.count() == 1
    assert AuditLog.objects.filter(action=brands.BRAND_PROPOSED).count() == 2


def test_a_rejected_name_can_be_proposed_again_with_a_wikidata_id() -> None:
    _rejected("Acme", wikidata_id="Q42")
    vendor = VendorFactory.create()
    outcome = brands.propose_brand(vendor, "Acme Motors", ctx=CTX, wikidata_id="Q42")
    assert outcome.brand is not None
    assert outcome.brand.status == Brand.Status.PENDING
    assert outcome.brand.wikidata_id == "Q42"
    assert Brand.objects.count() == 1


def test_a_dry_run_reproposal_changes_nothing() -> None:
    rejected = _rejected("Acme")
    outcome = brands.propose_brand(VendorFactory.create(), "Acme", ctx=CTX, dry_run=True)
    assert outcome.brand is None
    assert not outcome.changes.is_empty
    rejected.refresh_from_db()
    assert rejected.status == Brand.Status.REJECTED


def test_a_batch_reopens_a_rejected_name_instead_of_refusing_it() -> None:
    rejected = _rejected("Acme")
    vendor = VendorFactory.create()
    outcome = brands.propose_batch(vendor, ["Acme"], source="feed", ctx=CTX)
    assert outcome.refused == []
    rejected.refresh_from_db()
    assert rejected.status == Brand.Status.PENDING
    assert rejected.proposed_by == vendor
    assert rejected.batch is not None


def test_a_pending_name_is_still_refused_as_a_duplicate() -> None:
    brands.propose_brand(VendorFactory.create(), "Acme", ctx=CTX)
    with pytest.raises(ValidationError, match="similar name"):
        brands.propose_brand(VendorFactory.create(), "Acme", ctx=CTX)


def test_a_change_form_post_cannot_change_the_status(
    client: Client, superuser: User, mfa_login: MfaLogin
) -> None:
    staff = mfa_login(client, superuser)
    brand = brands.propose_brand(VendorFactory.create(), "Acme", ctx=CTX).brand
    assert brand is not None
    url = reverse("admin:catalog_brand_change", args=[brand.pk])
    staff.post(
        url,
        {
            "name": "Acme",
            "status": "rejected",
            "wikidata_id": "",
            "url": "",
            "same_as": "",
            "logo_alt": "",
            "translations-TOTAL_FORMS": "0",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
        },
    )
    brand.refresh_from_db()
    assert brand.status == Brand.Status.PENDING
