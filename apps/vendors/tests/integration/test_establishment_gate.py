"""CMP.13 (D-21): only vendors established in an allowed country (EU-27 by default) are activated.

The VEN.5 activation (`onboarding` → `active`) checks the registered office and the permanent
establishment of the VEN.12 identity against `PlatformOperator.allowed_vendor_countries`.
Applications from other countries are still received and kept.
"""

import datetime as dt
import secrets
from collections.abc import Callable

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, PlatformOperator
from apps.core.services import PLATFORM_OWNER_PERMISSION, set_allowed_vendor_countries
from apps.core.tests.factories import PlatformOperatorFactory
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.services import (
    active_vendors_outside,
    assert_establishment_allowed,
    change_status,
    submit_application,
    vendor_establishment,
)
from apps.vendors.tests.factories import ContractAcceptanceFactory, VendorFactory

pytestmark = pytest.mark.django_db

STAFF_CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")
REASON = "Owner decision"


def ready_to_activate(**fields: object) -> Vendor:
    """An onboarding vendor that accepted the contract in force: only the gate is left."""
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING, **fields)
    ContractAcceptanceFactory.create(vendor=vendor)
    return vendor


def platform_owner() -> User:
    user = UserFactory.create(is_staff=True)
    app_label, codename = PLATFORM_OWNER_PERMISSION.split(".")
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label=app_label, codename=codename)
    )
    return User.objects.get(pk=user.pk)


def configured_operator() -> PlatformOperator:
    return PlatformOperatorFactory.create(
        valid_from=timezone.now() - dt.timedelta(days=30), open_ended=True
    )


@pytest.mark.parametrize("country", ["RS", "CN"])
def test_a_vendor_registered_outside_the_eu_cannot_be_activated(country: str) -> None:
    configured_operator()
    vendor = ready_to_activate(country=country, registered_country=country)

    for dry_run in (True, False):
        with pytest.raises(engine.EstablishmentNotAllowed, match=country) as exc:
            change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX, dry_run=dry_run)
        assert "permanent establishment" in str(exc.value)

    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    assert not AuditLog.objects.filter(action="vendor.status_changed").exists()


def test_an_italian_vendor_is_activated() -> None:
    configured_operator()
    vendor = ready_to_activate(italian_company=True)
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE


def test_a_non_eu_vendor_with_a_permanent_establishment_in_the_eu_is_activated() -> None:
    vendor = ready_to_activate(country="CN", registered_country="CN", establishment_country="IT")
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE


def test_a_permanent_establishment_outside_the_set_is_refused_naming_both_countries() -> None:
    vendor = ready_to_activate(country="CN", registered_country="CN", establishment_country="CH")
    with pytest.raises(engine.EstablishmentNotAllowed, match="CN") as exc:
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    assert "CH" in str(exc.value)


def test_the_gate_follows_the_operator_setting() -> None:
    configured_operator()
    set_allowed_vendor_countries(["IT"], actor=platform_owner(), reason=REASON, ctx=STAFF_CTX)
    german = ready_to_activate(eu_company=True)
    with pytest.raises(engine.EstablishmentNotAllowed, match="DE"):
        assert_establishment_allowed(german)
    italian = ready_to_activate(italian_company=True)
    assert_establishment_allowed(italian)  # no error


def test_the_establishment_comes_from_the_legal_identity() -> None:
    vendor = VendorFactory.create(country="RS", registered_country="RS", establishment_country="SI")
    assert vendor_establishment(vendor) == engine.Establishment("RS", "SI")


def test_reinstating_a_suspended_vendor_is_not_gated() -> None:
    """Staff decide on vendors outside the set (spec 11 CMP.13): the gate is the activation."""
    vendor = VendorFactory.create(
        status=Vendor.Status.SUSPENDED, country="RS", registered_country="RS"
    )
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE


def test_an_application_from_outside_the_eu_is_still_received() -> None:
    user = UserFactory.create()
    vendor = Vendor(
        legal_name="Vendor R d.o.o.",
        display_name="Vendor R",
        country="RS",
        registered_country="RS",
        contact_email=f"vendor-r-{secrets.token_hex(3)}@example.com",
    )
    submit_application(vendor, user, ctx=STAFF_CTX)
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.APPLICANT


# --- Shrinking the set lists the active vendors outside it and suspends nobody


def test_shrinking_the_set_lists_the_active_vendors_outside_it() -> None:
    configured_operator()
    german = VendorFactory.create(status=Vendor.Status.ACTIVE, eu_company=True)
    VendorFactory.create(status=Vendor.Status.ACTIVE, italian_company=True)
    VendorFactory.create(status=Vendor.Status.ONBOARDING, eu_company=True)  # not active

    changeset = set_allowed_vendor_countries(
        ["IT"], actor=platform_owner(), reason=REASON, ctx=STAFF_CTX
    )

    assert [row["vendor"] for row in changeset.vendors_outside] == [str(german.public_id)]
    assert changeset.vendors_outside[0]["registered_office"] == "DE"
    assert changeset.to_dict()["vendors_outside"] == changeset.vendors_outside
    german.refresh_from_db()
    assert german.status == Vendor.Status.ACTIVE


def test_active_vendors_outside_a_set() -> None:
    inside = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        country="CN",
        registered_country="CN",
        establishment_country="IT",
    )
    outside = VendorFactory.create(status=Vendor.Status.ACTIVE, country="RS")
    rows = active_vendors_outside(frozenset({"IT"}))
    assert [row["vendor"] for row in rows] == [str(outside.public_id)]
    assert str(inside.public_id) not in {row["vendor"] for row in rows}


# --- Admin


def test_staff_activating_a_vendor_outside_the_set_see_the_reason(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> None:
    vendor = ready_to_activate(country="RS", registered_country="RS")
    staff = mfa_login(client, superuser)
    response = staff.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "move_to_active", "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    assert "registered office is in RS" in response.content.decode()
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
