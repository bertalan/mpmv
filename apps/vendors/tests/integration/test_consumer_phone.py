"""CMP.41 follow-up (owner decision 2026-11-19): a seller who sells to consumers cannot be
activated without a phone number, which is stored normalised. Fictional numbers only: the UK
drama range +44 20 7946 0xxx (Ofcom)."""

import pytest
from django.core.exceptions import ValidationError

from apps.core.audit import AuditContext
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, Vendor
from apps.vendors.services import (
    change_status,
    consumer_phone_missing,
    platform_duties_missing,
    save_vendor,
    sells_to_consumers,
    trader_contact,
)
from apps.vendors.tests.factories import ContractAcceptanceFactory, VendorFactory
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")
PHONE = "+44 20 7946 0123"


def ready_to_activate(**fields: object) -> Vendor:
    """An onboarding EU vendor that accepted the contract and certified, with the `activation`
    phone row switched off by staff: only the consumer phone rule can still block it."""
    seed_legal_requirements()
    LegalFieldRequirement.objects.filter(field_name="phone").update(active=False)
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING, eu_company=True, **fields)
    ContractAcceptanceFactory.create(vendor=vendor)
    return vendor


def test_every_vendor_sells_to_consumers_while_no_business_only_seller_exists() -> None:
    assert sells_to_consumers(VendorFactory.create())


def test_a_seller_to_consumers_without_a_phone_cannot_be_activated() -> None:
    vendor = ready_to_activate(phone="")
    assert consumer_phone_missing(vendor)
    assert platform_duties_missing(vendor) == ("phone",)
    with pytest.raises(engine.ActivationNotAllowed) as caught:
        change_status(vendor, engine.ACTIVE, ctx=CTX)
    assert "phone" in str(caught.value)
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.ONBOARDING


def test_the_same_seller_with_a_phone_is_activated() -> None:
    vendor = ready_to_activate(phone=PHONE)
    change_status(vendor, engine.ACTIVE, ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.ACTIVE


def test_the_phone_is_stored_normalised() -> None:
    vendor = VendorFactory.build(phone="0044 (20) 7946-0123")
    save_vendor(vendor, ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).phone == "+442079460123"


def test_an_implausible_phone_is_refused() -> None:
    vendor = VendorFactory.build(phone="call me")
    with pytest.raises(ValidationError) as caught:
        save_vendor(vendor, ctx=CTX)
    assert "phone" in caught.value.message_dict
    assert not Vendor.objects.filter(display_name=vendor.display_name).exists()


def test_an_active_seller_without_a_phone_keeps_its_contact_with_no_phone_line() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, phone="")
    assert consumer_phone_missing(vendor)
    assert trader_contact(vendor).phone == ""
