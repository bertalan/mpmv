"""VEN.2: VendorPlan and the versioned CommissionRule (spec 04), against real PostgreSQL.

Done when: tests on overlapping validity periods; no implicit zero rule; a rule is versioned and
never edited once written (orders snapshot it: `snapshot()` is the JSON they store).
"""

import datetime as dt
import functools
import json
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine, services
from apps.vendors.models import CommissionRule, VendorPlan
from apps.vendors.services import (
    COMMISSION_RULE_ADDED,
    PLAN_SAVED,
    add_commission_rule,
    commission_rule_for,
    save_plan,
)
from apps.vendors.tests.factories import CommissionRuleFactory, VendorFactory, VendorPlanFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
# Most tests are about validity periods in the past: they import historical rules explicitly.
add_rule = functools.partial(
    add_commission_rule, backdate=True, backdate_reason="historical import (test)"
)


def utc(year: int, month: int = 1) -> dt.datetime:
    return dt.datetime(year, month, 1, tzinfo=dt.UTC)


# --- Plans are data


def test_a_plan_is_saved_with_an_audit_entry_and_a_dry_run_changes_nothing() -> None:
    plan = VendorPlan(code="basic", name="Basic", max_products=50, features={"featured": False})
    changes = save_plan(plan, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not VendorPlan.objects.exists()

    save_plan(plan, ctx=CTX)
    assert VendorPlan.objects.get(code="basic").max_products == 50
    assert AuditLog.objects.filter(action=PLAN_SAVED, target_key=str(plan.public_id)).exists()


def test_a_plan_without_a_product_limit_is_unlimited_and_has_no_fee_at_launch() -> None:
    plan = VendorPlanFactory.create(code="advanced")
    assert plan.max_products is None
    assert plan.monthly_fee is None  # D-04: no paid-plan fee at launch


def test_plan_codes_are_unique() -> None:
    VendorPlanFactory.create(code="special")
    with pytest.raises(ValidationError):
        save_plan(VendorPlan(code="special", name="Again"), ctx=CTX)


def test_a_plan_refuses_a_negative_fee_a_negative_limit_and_non_object_features() -> None:
    for values in (
        {"monthly_fee": Decimal("-1.00")},
        {"max_products": -1},
        {"features": ["featured"]},
    ):
        with pytest.raises(ValidationError):
            save_plan(VendorPlan(code="bad", name="Bad", **values), ctx=CTX)


# --- No implicit zero rule, ranges


def test_a_zero_percent_rule_needs_an_explicit_promo_label_in_the_service() -> None:
    with pytest.raises(ValidationError) as excinfo:
        add_rule(CommissionRuleFactory.build(percent=Decimal("0"), promo_label=""), ctx=CTX)
    assert "percent" in excinfo.value.message_dict
    add_rule(
        CommissionRuleFactory.build(percent=Decimal("0"), promo_label="operator-owned"), ctx=CTX
    )


def test_the_database_refuses_an_implicit_zero_rule_and_out_of_range_values() -> None:
    for values in (
        {"percent": Decimal("0"), "promo_label": ""},
        {"percent": Decimal("100.01")},
        {"fee_percent": Decimal("-1")},
        {"fee_fixed": Decimal("-0.01")},
        {"base": "gross"},
        {"fee_base": "net_goods"},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            CommissionRuleFactory.create(**values)


def test_rates_are_decimals_with_two_places() -> None:
    rule = CommissionRuleFactory.create(percent=Decimal("19.995"))
    rule.refresh_from_db()
    assert rule.percent == Decimal("20.00")
    assert isinstance(rule.fee_fixed, Decimal)


# --- Validity periods never overlap


def test_two_rules_of_the_same_scope_cannot_overlap_in_the_database() -> None:
    CommissionRuleFactory.create(valid_from=utc(2026), valid_to=utc(2027))
    with pytest.raises(IntegrityError), transaction.atomic():
        CommissionRuleFactory.create(valid_from=utc(2026, 6), valid_to=None)


def test_adjacent_periods_do_not_overlap() -> None:
    CommissionRuleFactory.create(valid_from=utc(2026), valid_to=utc(2027), version=1)
    CommissionRuleFactory.create(valid_from=utc(2027), valid_to=None, version=2)


def test_rules_of_different_scopes_may_cover_the_same_period() -> None:
    vendor, plan = VendorFactory.create(), VendorPlanFactory.create()
    CommissionRuleFactory.create(valid_from=utc(2026))
    CommissionRuleFactory.create(valid_from=utc(2026), vendor=vendor)
    CommissionRuleFactory.create(valid_from=utc(2026), plan=plan)


def test_an_end_before_the_start_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        CommissionRuleFactory.create(valid_from=utc(2027), valid_to=utc(2026))


# --- Versions: a change is a new row


def test_a_new_rule_closes_the_open_one_and_gets_the_next_version() -> None:
    first = CommissionRuleFactory.build(valid_from=utc(2026), percent=Decimal("20"))
    add_rule(first, ctx=CTX)
    second = CommissionRuleFactory.build(valid_from=utc(2027), percent=Decimal("18"))
    changes = add_rule(second, ctx=CTX)

    assert changes.summary() == {"create": 1, "update": 1, "delete": 0}
    old, new = CommissionRule.objects.order_by("version")
    assert (old.version, new.version) == (1, 2)
    assert old.valid_to == utc(2027)
    assert old.percent == Decimal("20.00")  # never edited
    assert new.valid_to is None


def test_versions_count_per_scope() -> None:
    vendor = VendorFactory.create()
    add_rule(CommissionRuleFactory.build(valid_from=utc(2026)), ctx=CTX)
    add_rule(CommissionRuleFactory.build(valid_from=utc(2026), vendor=vendor), ctx=CTX)
    assert set(CommissionRule.objects.values_list("version", flat=True)) == {1}


def test_a_new_rule_must_start_after_the_latest_one() -> None:
    add_rule(CommissionRuleFactory.build(valid_from=utc(2027)), ctx=CTX)
    with pytest.raises(ValidationError) as excinfo:
        add_rule(CommissionRuleFactory.build(valid_from=utc(2026)), ctx=CTX)
    assert "valid_from" in excinfo.value.message_dict


def test_a_dry_run_returns_the_change_and_writes_nothing() -> None:
    changes = add_rule(CommissionRuleFactory.build(), ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not CommissionRule.objects.exists()
    assert not AuditLog.objects.filter(action=COMMISSION_RULE_ADDED).exists()


def test_adding_a_rule_is_audited_with_its_rate() -> None:
    rule = CommissionRuleFactory.build(percent=Decimal("17.50"))
    add_rule(rule, ctx=CTX)
    entry = AuditLog.objects.get(action=COMMISSION_RULE_ADDED)
    assert entry.actor_label == "staff:ops"
    assert (entry.after or {})["percent"] == "17.50"


def test_a_vendor_scoped_rule_for_a_vendor_and_a_plan_together_is_refused() -> None:
    with pytest.raises(ValidationError):
        add_rule(
            CommissionRuleFactory.build(
                vendor=VendorFactory.create(), plan=VendorPlanFactory.create()
            ),
            ctx=CTX,
        )


# --- Immutable once written (database trigger)


def test_a_rule_cannot_be_deleted_even_with_raw_sql() -> None:
    rule = CommissionRuleFactory.create()
    with pytest.raises(ProgrammingError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM vendors_commissionrule WHERE id = %s", [rule.pk])
    assert CommissionRule.objects.filter(pk=rule.pk).exists()


def test_the_rates_of_a_rule_cannot_be_updated_even_with_a_queryset_update() -> None:
    rule = CommissionRuleFactory.create(percent=Decimal("20"))
    for values in (
        {"percent": Decimal("5")},
        {"fee_fixed": Decimal("0")},
        {"promo_label": "x"},
        {"valid_from": utc(2020)},
        {"version": 9},
    ):
        with pytest.raises(ProgrammingError), transaction.atomic():
            CommissionRule.objects.filter(pk=rule.pk).update(**values)
    rule.refresh_from_db()
    assert rule.percent == Decimal("20.00")


def test_closing_is_the_only_update_and_only_once() -> None:
    rule = CommissionRuleFactory.create(valid_from=utc(2026), valid_to=None)
    CommissionRule.objects.filter(pk=rule.pk).update(valid_to=utc(2027))
    for end in (utc(2028), None):
        with pytest.raises(ProgrammingError), transaction.atomic():
            CommissionRule.objects.filter(pk=rule.pk).update(valid_to=end)
    rule.refresh_from_db()
    assert rule.valid_to == utc(2027)


def test_saving_an_existing_rule_instance_is_refused_by_the_model() -> None:
    rule = CommissionRuleFactory.create()
    rule.percent = Decimal("1")
    with pytest.raises(PermissionError):
        rule.save()


# --- Which rule applies


def test_the_rule_for_a_vendor_is_its_own_then_its_plans_then_the_default() -> None:
    vendor, plan = VendorFactory.create(), VendorPlanFactory.create()
    default = CommissionRuleFactory.create(valid_from=utc(2026))
    assert commission_rule_for(vendor, at=utc(2026, 6), plan=plan) == default
    by_plan = CommissionRuleFactory.create(valid_from=utc(2026), plan=plan)
    assert commission_rule_for(vendor, at=utc(2026, 6), plan=plan) == by_plan
    own = CommissionRuleFactory.create(valid_from=utc(2026), vendor=vendor)
    assert commission_rule_for(vendor, at=utc(2026, 6), plan=plan) == own
    assert commission_rule_for(VendorFactory.create(), at=utc(2026, 6), plan=plan) == by_plan


def test_a_missing_rule_is_a_blocking_error_not_a_zero_commission() -> None:
    with pytest.raises(engine.NoCommissionRule):
        commission_rule_for(VendorFactory.create(), at=utc(2026))


def test_the_rule_in_force_depends_on_the_date() -> None:
    vendor = VendorFactory.create()
    add_rule(CommissionRuleFactory.build(valid_from=utc(2026), percent=20), ctx=CTX)
    add_rule(CommissionRuleFactory.build(valid_from=utc(2027), percent=18), ctx=CTX)
    assert commission_rule_for(vendor, at=utc(2026, 6)).version == 1
    assert commission_rule_for(vendor, at=utc(2027, 6)).version == 2


# --- The snapshot orders store (ORD.2)


def test_the_snapshot_carries_the_rule_id_the_version_and_every_rate_as_json() -> None:
    rule = CommissionRuleFactory.create(
        percent=Decimal("20"),
        base=engine.NET_GOODS,
        fee_percent=Decimal("4"),
        fee_fixed=Decimal("2"),
        promo_label="launch promo",
        valid_from=utc(2026),
        version=3,
    )
    snapshot = rule.snapshot()
    assert snapshot == {
        "rule_id": str(rule.public_id),
        "version": 3,
        "percent": "20.00",
        "base": "net_goods",
        "fee_percent": "4.00",
        "fee_fixed": "2.00",
        "fee_base": "gross_paid",
        "promo_label": "launch promo",
        "valid_from": "2026-01-01T00:00:00+00:00",
    }
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_a_snapshot_does_not_follow_the_live_rule() -> None:
    rule = CommissionRuleFactory.create(valid_from=utc(2026), valid_to=None)
    snapshot = rule.snapshot()
    CommissionRule.objects.filter(pk=rule.pk).update(valid_to=utc(2027))
    assert rule.snapshot() == snapshot  # the closing date is no part of what an order froze


# --- Admin


def test_staff_add_a_rule_in_the_admin_and_cannot_edit_or_delete_one(
    client: Client, mfa_login: Any
) -> None:
    staff: Client = mfa_login(client, role_user("config"))
    url = reverse("admin:vendors_commissionrule_add")
    data = {
        "percent": "15.00",
        "base": "net_goods",
        "fee_percent": "4.00",
        "fee_fixed": "2.00",
        "fee_base": "gross_paid",
        "promo_label": "",
        "valid_from_0": "2030-01-01",
        "valid_from_1": "00:00:00",
    }
    assert staff.post(url, data).status_code == 302
    rule = CommissionRule.objects.get()
    assert (rule.version, rule.percent) == (1, Decimal("15.00"))
    assert AuditLog.objects.filter(action=COMMISSION_RULE_ADDED).exists()

    refused = staff.post(url, {**data, "percent": "0.00", "valid_from_0": "2031-01-01"})
    assert refused.status_code == 200  # an implicit zero is a form error, not a crash
    assert CommissionRule.objects.count() == 1

    assert (
        staff.get(reverse("admin:vendors_commissionrule_delete", args=[rule.pk])).status_code == 403
    )
    assert (
        staff.post(reverse("admin:vendors_commissionrule_change", args=[rule.pk]), data).status_code
        == 403
    )


def test_the_admin_reads_the_start_in_business_time_not_utc(client: Client, mfa_login: Any) -> None:
    staff: Client = mfa_login(client, role_user("config"))
    data = {
        "percent": "15.00",
        "base": "net_goods",
        "fee_percent": "4.00",
        "fee_fixed": "2.00",
        "fee_base": "gross_paid",
        "promo_label": "",
        "valid_from_0": "2030-01-01",
        "valid_from_1": "00:00:00",
    }
    assert staff.post(reverse("admin:vendors_commissionrule_add"), data).status_code == 302
    # "From 1 January" typed by staff is midnight in Rome, 23:00 UTC the day before.
    assert CommissionRule.objects.get().valid_from == dt.datetime(2029, 12, 31, 23, tzinfo=dt.UTC)


def test_the_admin_form_never_offers_backdating(client: Client, mfa_login: Any) -> None:
    staff: Client = mfa_login(client, role_user("config"))
    page = staff.get(reverse("admin:vendors_commissionrule_add")).content.decode()
    assert "backdate" not in page
    past = {
        "percent": "15.00",
        "base": "net_goods",
        "fee_percent": "4.00",
        "fee_fixed": "2.00",
        "fee_base": "gross_paid",
        "promo_label": "",
        "valid_from_0": "2020-01-01",
        "valid_from_1": "00:00:00",
    }
    response = staff.post(reverse("admin:vendors_commissionrule_add"), past)
    assert response.status_code == 200
    assert not CommissionRule.objects.exists()


# --- No backdating (review of #225): orders priced with a rule must keep finding it


def future(days: int = 30) -> dt.datetime:
    return timezone.now() + dt.timedelta(days=days)


def test_a_rule_starting_in_the_past_is_refused_by_default() -> None:
    with pytest.raises(ValidationError) as excinfo:
        add_commission_rule(
            CommissionRuleFactory.build(valid_from=timezone.now() - dt.timedelta(days=1)), ctx=CTX
        )
    assert "valid_from" in excinfo.value.message_dict
    assert not CommissionRule.objects.exists()


def test_a_rule_from_now_or_later_is_accepted() -> None:
    add_commission_rule(CommissionRuleFactory.build(valid_from=timezone.now()), ctx=CTX)
    add_commission_rule(CommissionRuleFactory.build(valid_from=future()), ctx=CTX)
    assert CommissionRule.objects.count() == 2


def test_backdating_needs_the_flag_and_a_reason_and_is_audited() -> None:
    past = timezone.now() - dt.timedelta(days=400)
    with pytest.raises(ValidationError):
        add_commission_rule(CommissionRuleFactory.build(valid_from=past), ctx=CTX, backdate=True)
    with pytest.raises(ValidationError):
        add_commission_rule(
            CommissionRuleFactory.build(valid_from=past),
            ctx=CTX,
            backdate=True,
            backdate_reason="   ",
        )
    add_commission_rule(
        CommissionRuleFactory.build(valid_from=past),
        ctx=CTX,
        backdate=True,
        backdate_reason="MIG import of the 2025 rule",
    )
    entry = AuditLog.objects.get(action=COMMISSION_RULE_ADDED)
    assert (entry.after or {})["backdate_reason"] == "MIG import of the 2025 rule"


def test_a_normal_rule_has_no_backdate_reason_in_its_audit_entry() -> None:
    add_commission_rule(CommissionRuleFactory.build(valid_from=future()), ctx=CTX)
    assert "backdate_reason" not in (AuditLog.objects.get().after or {})


def test_backdating_a_dry_run_still_changes_nothing() -> None:
    changes = add_commission_rule(
        CommissionRuleFactory.build(valid_from=utc(2020)),
        ctx=CTX,
        dry_run=True,
        backdate=True,
        backdate_reason="import",
    )
    assert changes.summary()["create"] == 1
    assert not CommissionRule.objects.exists()


# --- Boundaries and gaps


def test_the_end_instant_is_the_next_version_and_before_the_start_is_none() -> None:
    vendor = VendorFactory.create()
    add_rule(CommissionRuleFactory.build(valid_from=utc(2026), percent=20), ctx=CTX)
    add_rule(CommissionRuleFactory.build(valid_from=utc(2027), percent=18), ctx=CTX)
    assert commission_rule_for(vendor, at=utc(2027)).version == 2  # at == valid_to of v1
    last_of_v1 = utc(2027) - dt.timedelta(microseconds=1)
    assert commission_rule_for(vendor, at=last_of_v1).version == 1
    with pytest.raises(engine.NoCommissionRule):
        commission_rule_for(vendor, at=utc(2026) - dt.timedelta(microseconds=1))


def test_a_rule_cannot_leave_a_gap_after_a_closed_one() -> None:
    CommissionRuleFactory.create(valid_from=utc(2026), valid_to=utc(2027), version=1)
    with pytest.raises(ValidationError) as excinfo:
        add_rule(CommissionRuleFactory.build(valid_from=utc(2028)), ctx=CTX)
    assert "valid_from" in excinfo.value.message_dict
    add_rule(CommissionRuleFactory.build(valid_from=utc(2027)), ctx=CTX)  # exactly at the end


# --- CORE.25 session level and the Configuration and tax role (D-32 sensitive, `commission_change`)


def role_user(key: str) -> User:
    """A staff user in the platform role group of `key`; roles come from the CORE.18 matrix. The
    Manager sits above this app, so its `roles` module is reached by name."""
    group = import_string("apps.manager.roles.role")(key).group
    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


RULE_FORM = {
    "percent": "15.00",
    "base": "net_goods",
    "fee_percent": "4.00",
    "fee_fixed": "2.00",
    "fee_base": "gross_paid",
    "promo_label": "",
    "valid_from_0": "2030-01-01",
    "valid_from_1": "00:00:00",
}
PLAN_FORM = {"code": "gold", "name": "Gold", "max_products": "10", "features": "{}"}


def test_a_restricted_session_is_refused_on_a_rule_and_a_plan_change(client: Client) -> None:
    verified_login(client, role_user("config"), passkey=True, level=levels.NO_WINDOW)
    rule_url = reverse("admin:vendors_commissionrule_add")
    assert client.get(rule_url).status_code == 403
    assert client.post(rule_url, RULE_FORM).status_code == 403
    assert not CommissionRule.objects.exists()
    client = Client()
    verified_login(client, role_user("owner"), passkey=True, level=levels.NO_WINDOW)
    assert client.post(reverse("admin:vendors_vendorplan_add"), PLAN_FORM).status_code == 403
    assert not VendorPlan.objects.filter(code="gold").exists()


def test_the_configuration_role_adds_rules_and_the_platform_owner_adds_plans(
    client: Client,
) -> None:
    verified_login(client, role_user("config"), passkey=True)  # operational by default
    assert client.post(reverse("admin:vendors_commissionrule_add"), RULE_FORM).status_code == 302
    assert CommissionRule.objects.count() == 1
    client = Client()
    verified_login(client, role_user("owner"), passkey=True)
    assert client.post(reverse("admin:vendors_vendorplan_add"), PLAN_FORM).status_code == 302
    assert VendorPlan.objects.filter(code="gold").exists()


def test_a_plan_change_needs_the_operational_session_too(client: Client) -> None:
    plan = VendorPlanFactory.create(code="silver")
    url = reverse("admin:vendors_vendorplan_change", args=[plan.pk])
    verified_login(client, role_user("owner"), passkey=True, level=levels.NO_WINDOW)
    assert client.post(url, {**PLAN_FORM, "code": "silver", "name": "Changed"}).status_code == 403
    plan.refresh_from_db()
    assert plan.name != "Changed"


@pytest.mark.parametrize("key", ["finance", "catalog", "support", "owner"])
def test_other_roles_cannot_add_a_commission_rule(client: Client, key: str) -> None:
    verified_login(client, role_user(key), passkey=True)
    assert client.post(reverse("admin:vendors_commissionrule_add"), RULE_FORM).status_code == 403
    assert not CommissionRule.objects.exists()


@pytest.mark.parametrize("key", ["config", "finance", "catalog", "support"])
def test_only_the_platform_owner_edits_plans(client: Client, key: str) -> None:
    verified_login(client, role_user(key), passkey=True)
    assert client.post(reverse("admin:vendors_vendorplan_add"), PLAN_FORM).status_code == 403
    assert not VendorPlan.objects.filter(code="gold").exists()


def test_a_superuser_without_a_role_has_no_access(client: Client, superuser: User) -> None:
    verified_login(client, superuser, passkey=True)
    assert client.post(reverse("admin:vendors_commissionrule_add"), RULE_FORM).status_code == 403
    assert client.post(reverse("admin:vendors_vendorplan_add"), PLAN_FORM).status_code == 403
    assert not CommissionRule.objects.exists()


# --- Edge cases of rounding, boundaries and races

ROME = ZoneInfo("Europe/Rome")


def test_the_snapshot_rounds_half_up_not_half_even() -> None:
    rule = CommissionRuleFactory.build(
        percent=Decimal("20.005"), fee_percent=Decimal("2.675"), fee_fixed=Decimal("0.005")
    )
    snapshot = rule.snapshot()
    assert (snapshot["percent"], snapshot["fee_percent"], snapshot["fee_fixed"]) == (
        "20.01",
        "2.68",
        "0.01",
    )


def test_a_rate_with_a_third_decimal_is_refused_not_rounded_by_the_database() -> None:
    for field in ("percent", "fee_percent", "fee_fixed"):
        rule = CommissionRuleFactory.build(valid_from=utc(2026), **{field: Decimal("1.005")})
        with pytest.raises(ValidationError):
            add_rule(rule, ctx=CTX)
    assert not CommissionRule.objects.exists()


@pytest.mark.parametrize(
    ("day", "hours_in_day"),
    [(dt.date(2027, 3, 28), 23), (dt.date(2026, 10, 25), 25), (dt.date(2026, 7, 1), 24)],
)
def test_a_rule_switches_exactly_at_business_midnight_also_on_a_dst_change_day(
    day: dt.date, hours_in_day: int
) -> None:
    midnight = dt.datetime.combine(day, dt.time(), tzinfo=ROME)
    next_midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=ROME)
    assert (next_midnight.astimezone(dt.UTC) - midnight.astimezone(dt.UTC)).total_seconds() == (
        hours_in_day * 3600
    )
    vendor = VendorFactory.create()
    add_rule(CommissionRuleFactory.build(valid_from=utc(2026), percent=20), ctx=CTX)
    add_rule(CommissionRuleFactory.build(valid_from=next_midnight, percent=18), ctx=CTX)
    just_before = next_midnight - dt.timedelta(microseconds=1)
    assert commission_rule_for(vendor, at=midnight).version == 1
    assert commission_rule_for(vendor, at=just_before).version == 1
    assert commission_rule_for(vendor, at=next_midnight).version == 2


def test_two_first_rules_of_a_scope_at_once_give_a_form_error_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_rule(CommissionRuleFactory.build(valid_from=utc(2026)), ctx=CTX)
    # The loser read the scope as empty (no row to lock) and so picks version 1 again.
    monkeypatch.setattr(services, "_rules_of_scope", lambda new: CommissionRule.objects.none())
    with pytest.raises(ValidationError) as excinfo:
        add_rule(CommissionRuleFactory.build(valid_from=utc(2026)), ctx=CTX)
    assert "valid_from" in excinfo.value.message_dict
    assert CommissionRule.objects.count() == 1
