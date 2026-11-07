"""CMP.13 (D-21): the countries where vendors may be established, a `PlatformOperator` setting.

Seeded with the EU-27, changed only through `set_allowed_vendor_countries` by a platform owner,
as a new operator version with an audit entry holding the set before and after and the reason.
"""

import datetime as dt
import secrets
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.countries import EU_MEMBER_STATES
from apps.core.models import AuditLog, PlatformOperator
from apps.core.services import (
    ALLOWED_VENDOR_COUNTRIES_CHANGED,
    PLATFORM_OWNER_PERMISSION,
    NoPlatformOperator,
    PlatformOwnerRequired,
    allowed_vendor_countries,
    configure_platform_operator,
    current_operator,
    set_allowed_vendor_countries,
)
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

EU_27 = sorted(EU_MEMBER_STATES)
CTX = AuditContext(actor_id=1, actor_label="staff:owner", source="admin", ip="127.0.0.1")
REASON = "Tax advisor confirmed the change"


def make_user(*, owner: bool = False, **fields: Any) -> Any:
    """A user of the configured user model (core must not import accounts)."""
    model = get_user_model()
    user = model.objects.create_user(
        email=f"user-{secrets.token_hex(4)}@example.com",
        password=secrets.token_urlsafe(16),
        **fields,
    )
    if owner:
        app_label, codename = PLATFORM_OWNER_PERMISSION.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
        user = model.objects.get(pk=user.pk)  # drop the permission cache
    return user


@pytest.fixture
def operator() -> PlatformOperator:
    return PlatformOperatorFactory.create(
        valid_from=timezone.now() - dt.timedelta(days=30), open_ended=True
    )


# --- Default


def test_a_new_operator_admits_vendors_from_the_eu_27() -> None:
    op = PlatformOperatorFactory.create()
    op.refresh_from_db()
    assert op.allowed_vendor_countries == EU_27


def test_without_an_operator_the_gate_uses_the_eu_27() -> None:
    assert allowed_vendor_countries() == frozenset(EU_MEMBER_STATES)


def test_the_gate_reads_the_operator_in_force(operator: PlatformOperator) -> None:
    set_allowed_vendor_countries(["IT", "AT"], actor=make_user(owner=True), reason=REASON, ctx=CTX)
    assert allowed_vendor_countries() == frozenset({"IT", "AT"})


# --- Changing the set


def test_an_owner_changes_the_set_as_a_new_operator_version(operator: PlatformOperator) -> None:
    changeset = set_allowed_vendor_countries(
        ["it", "de", "IT"], actor=make_user(owner=True), reason=REASON, ctx=CTX
    )

    assert changeset.summary() == {"create": 1, "update": 1, "delete": 0}
    old, new = PlatformOperator.objects.order_by("valid_from")
    assert old.pk == operator.pk
    assert old.allowed_vendor_countries == EU_27
    assert old.valid_to == new.valid_from
    assert new.valid_to is None
    assert new.allowed_vendor_countries == ["DE", "IT"]
    for name in ("legal_name", "country", "vat_number", "tax_regime", "dac7_authority_country"):
        assert getattr(new, name) == getattr(old, name)
    assert current_operator() == new


def test_changing_the_set_is_audited_with_before_after_and_reason(
    operator: PlatformOperator,
) -> None:
    set_allowed_vendor_countries(["IT", "DE"], actor=make_user(owner=True), reason=REASON, ctx=CTX)

    new = current_operator()
    entry = AuditLog.objects.get(
        action=ALLOWED_VENDOR_COUNTRIES_CHANGED, target_key=str(new.public_id)
    )
    assert entry.target_model == "core.platformoperator"
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["allowed_vendor_countries"] == EU_27
    assert entry.after["allowed_vendor_countries"] == ["DE", "IT"]
    assert entry.after["reason"] == REASON
    assert entry.actor_label == "staff:owner"
    closing = AuditLog.objects.get(
        action=ALLOWED_VENDOR_COUNTRIES_CHANGED, target_key=str(operator.public_id)
    )
    assert closing.after == {"valid_to": new.valid_from.isoformat()}


def test_a_dry_run_writes_nothing(operator: PlatformOperator) -> None:
    changeset = set_allowed_vendor_countries(
        ["IT"], actor=make_user(owner=True), reason=REASON, ctx=CTX, dry_run=True
    )
    assert changeset.summary() == {"create": 1, "update": 1, "delete": 0}
    assert PlatformOperator.objects.get() == operator
    assert PlatformOperator.objects.get().valid_to is None
    assert not AuditLog.objects.exists()


@pytest.mark.parametrize(
    "fields",
    [{}, {"is_staff": True}, {"is_staff": True, "is_active": False}],
    ids=["customer", "staff without the permission", "inactive staff"],
)
def test_a_user_without_the_platform_owner_permission_is_refused(
    operator: PlatformOperator, fields: dict[str, Any]
) -> None:
    user = make_user(**fields)
    for dry_run in (True, False):
        with pytest.raises(PlatformOwnerRequired, match="platform owner"):
            set_allowed_vendor_countries(
                ["IT"], actor=user, reason=REASON, ctx=CTX, dry_run=dry_run
            )
    assert PlatformOperator.objects.count() == 1
    assert not AuditLog.objects.exists()


def test_an_inactive_platform_owner_is_refused(operator: PlatformOperator) -> None:
    user = make_user(owner=True, is_active=False)
    with pytest.raises(PlatformOwnerRequired):
        set_allowed_vendor_countries(["IT"], actor=user, reason=REASON, ctx=CTX)


def test_the_refusal_is_a_permission_denied_for_the_adapters() -> None:
    assert issubclass(PlatformOwnerRequired, PermissionDenied)


@pytest.mark.parametrize(
    ("countries", "reason", "field"),
    [
        (["IT", "ZZ"], REASON, "allowed_vendor_countries"),
        ([], REASON, "allowed_vendor_countries"),
        (["IT"], "  ", "reason"),
    ],
    ids=["unknown country", "empty set", "no reason"],
)
def test_an_invalid_change_is_refused_before_writing(
    operator: PlatformOperator, countries: list[str], reason: str, field: str
) -> None:
    with pytest.raises(ValidationError) as exc:
        set_allowed_vendor_countries(countries, actor=make_user(owner=True), reason=reason, ctx=CTX)
    assert field in exc.value.message_dict
    assert PlatformOperator.objects.count() == 1
    assert not AuditLog.objects.exists()


def test_the_same_set_changes_nothing(operator: PlatformOperator) -> None:
    changeset = set_allowed_vendor_countries(
        EU_27, actor=make_user(owner=True), reason=REASON, ctx=CTX
    )
    assert changeset.is_empty
    assert PlatformOperator.objects.count() == 1
    assert not AuditLog.objects.exists()


def test_the_set_cannot_change_before_an_operator_is_configured() -> None:
    with pytest.raises(NoPlatformOperator):
        set_allowed_vendor_countries(["IT"], actor=make_user(owner=True), reason=REASON, ctx=CTX)


@override_settings(VENDORS_OUTSIDE_COUNTRIES_HOOK="")
def test_without_a_vendors_hook_no_vendor_is_listed(operator: PlatformOperator) -> None:
    changeset = set_allowed_vendor_countries(
        ["IT"], actor=make_user(owner=True), reason=REASON, ctx=CTX, dry_run=True
    )
    assert changeset.vendors_outside == []


# --- Admin


def test_the_admin_shows_the_set_and_never_offers_it_on_a_new_row(
    client: Client, superuser: Any, mfa_login: Callable[[Client, Any], Client]
) -> None:
    # `superuser` and `mfa_login` come from the root conftest: core must not import accounts.
    staff = mfa_login(client, superuser)
    op = PlatformOperatorFactory.create(allowed_vendor_countries=["AT", "IT"])
    page = staff.get(reverse("admin:core_platformoperator_change", args=[op.pk]))
    assert page.status_code == 200
    assert "Allowed vendor establishment countries" in page.content.decode()
    add = staff.get(reverse("admin:core_platformoperator_add")).content.decode()
    assert "allowed_vendor_countries" not in add


# --- Other operator changes keep the set


def test_reconfiguring_the_operator_carries_the_set_over(operator: PlatformOperator) -> None:
    set_allowed_vendor_countries(["IT"], actor=make_user(owner=True), reason=REASON, ctx=CTX)
    relocated = PlatformOperatorFactory.build(
        country="DE",
        dac7_authority_country="DE",
        valid_from=timezone.now() + dt.timedelta(days=1),
        allowed_vendor_countries=EU_27,  # whatever the caller sets, only the service changes it
    )
    configure_platform_operator(relocated, ctx=CTX)

    stored = PlatformOperator.objects.get(pk=relocated.pk)
    assert stored.allowed_vendor_countries == ["IT"]
