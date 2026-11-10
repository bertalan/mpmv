"""VEN.2: VendorPlan and the versioned CommissionRule (spec 04), against real PostgreSQL.

Done when: tests on overlapping validity periods; no implicit zero rule; a rule is versioned and
never edited once written (orders snapshot it: `snapshot()` is the JSON they store).
"""

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
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
        add_commission_rule(
            CommissionRuleFactory.build(percent=Decimal("0"), promo_label=""), ctx=CTX
        )
    assert "percent" in excinfo.value.message_dict
    add_commission_rule(
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
    add_commission_rule(first, ctx=CTX)
    second = CommissionRuleFactory.build(valid_from=utc(2027), percent=Decimal("18"))
    changes = add_commission_rule(second, ctx=CTX)

    assert changes.summary() == {"create": 1, "update": 1, "delete": 0}
    old, new = CommissionRule.objects.order_by("version")
    assert (old.version, new.version) == (1, 2)
    assert old.valid_to == utc(2027)
    assert old.percent == Decimal("20.00")  # never edited
    assert new.valid_to is None


def test_versions_count_per_scope() -> None:
    vendor = VendorFactory.create()
    add_commission_rule(CommissionRuleFactory.build(valid_from=utc(2026)), ctx=CTX)
    add_commission_rule(CommissionRuleFactory.build(valid_from=utc(2026), vendor=vendor), ctx=CTX)
    assert set(CommissionRule.objects.values_list("version", flat=True)) == {1}


def test_a_new_rule_must_start_after_the_latest_one() -> None:
    add_commission_rule(CommissionRuleFactory.build(valid_from=utc(2027)), ctx=CTX)
    with pytest.raises(ValidationError) as excinfo:
        add_commission_rule(CommissionRuleFactory.build(valid_from=utc(2026)), ctx=CTX)
    assert "valid_from" in excinfo.value.message_dict


def test_a_dry_run_returns_the_change_and_writes_nothing() -> None:
    changes = add_commission_rule(CommissionRuleFactory.build(), ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not CommissionRule.objects.exists()
    assert not AuditLog.objects.filter(action=COMMISSION_RULE_ADDED).exists()


def test_adding_a_rule_is_audited_with_its_rate() -> None:
    rule = CommissionRuleFactory.build(percent=Decimal("17.50"))
    add_commission_rule(rule, ctx=CTX)
    entry = AuditLog.objects.get(action=COMMISSION_RULE_ADDED)
    assert entry.actor_label == "staff:ops"
    assert (entry.after or {})["percent"] == "17.50"


def test_a_vendor_scoped_rule_for_a_vendor_and_a_plan_together_is_refused() -> None:
    with pytest.raises(ValidationError):
        add_commission_rule(
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
    rule = CommissionRuleFactory.create(valid_from=utc(2026))
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
    add_commission_rule(CommissionRuleFactory.build(valid_from=utc(2026), percent=20), ctx=CTX)
    add_commission_rule(CommissionRuleFactory.build(valid_from=utc(2027), percent=18), ctx=CTX)
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
    rule = CommissionRuleFactory.create(valid_from=utc(2026))
    snapshot = rule.snapshot()
    CommissionRule.objects.filter(pk=rule.pk).update(valid_to=utc(2027))
    assert rule.snapshot() == snapshot  # the closing date is no part of what an order froze


# --- Admin


def test_staff_add_a_rule_in_the_admin_and_cannot_edit_or_delete_one(
    client: Client, superuser: User, mfa_login: Any
) -> None:
    staff: Client = mfa_login(client, superuser)
    url = reverse("admin:vendors_commissionrule_add")
    data = {
        "percent": "15.00",
        "base": "net_goods",
        "fee_percent": "4.00",
        "fee_fixed": "2.00",
        "fee_base": "gross_paid",
        "promo_label": "",
        "valid_from_0": "2026-01-01",
        "valid_from_1": "00:00:00",
    }
    assert staff.post(url, data).status_code == 302
    rule = CommissionRule.objects.get()
    assert (rule.version, rule.percent) == (1, Decimal("15.00"))
    assert AuditLog.objects.filter(action=COMMISSION_RULE_ADDED).exists()

    refused = staff.post(url, {**data, "percent": "0.00", "valid_from_0": "2027-01-01"})
    assert refused.status_code == 200  # an implicit zero is a form error, not a crash
    assert CommissionRule.objects.count() == 1

    assert (
        staff.get(reverse("admin:vendors_commissionrule_delete", args=[rule.pk])).status_code == 403
    )
    assert (
        staff.post(reverse("admin:vendors_commissionrule_change", args=[rule.pk]), data).status_code
        == 403
    )
