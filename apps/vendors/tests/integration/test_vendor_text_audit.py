"""VEN.39 (owner decision Q-36): every change to a vendor text, in any language, through any
door, writes one `vendor.profile_changed` entry with the changed fields before and after."""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import (
    audit_vendor_text_changes,
    save_storefront_profile,
    vendor_text_snapshot,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from apps.vendors.tests.integration.test_admin import vendor_form
from tests.i18n import other_language

CHANGED = "vendor.profile_changed"
CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
ADMIN_CTX = AuditContext(actor_label="user:staff@example.com", source="admin")
MfaLogin = Callable[[Client, User], Client]


def entries(vendor: Vendor) -> list[AuditLog]:
    found = AuditLog.objects.filter(action=CHANGED, target_key__startswith=str(vendor.public_id))
    return list(found.order_by("id"))


def before_of(entry: AuditLog) -> dict[str, Any]:
    assert entry.before is not None
    return dict(entry.before)


def after_of(entry: AuditLog) -> dict[str, Any]:
    assert entry.after is not None
    return dict(entry.after)


def by_language(vendor: Vendor) -> dict[str, AuditLog]:
    """The vendor's text entries by language; fails when a language was audited twice."""
    found = entries(vendor)
    result = {entry.target_key.split("/")[1]: entry for entry in found}
    assert len(result) == len(found), "a change was audited twice"
    return result


def inline(index: int, language: str, **texts: str) -> dict[str, str]:
    data = {
        f"translations-{index}-language": language,
        f"translations-{index}-status": "published",
    }
    data.update({f"translations-{index}-{name}": value for name, value in texts.items()})
    return data


@pytest.mark.django_db
def test_manager_return_policy_in_two_languages_gives_two_small_entries() -> None:
    vendor = VendorFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    policies = {primary_language(): "<p>14 days</p>", other_language(): "<p>14 g</p>"}
    for language, text in policies.items():
        save_storefront_profile(vendor, language, {"refund_policy": text}, actor=owner, ctx=CTX)
    found = by_language(vendor)
    assert set(found) == {primary_language(), other_language()}
    after = after_of(found[other_language()])
    assert after["refund_policy"] == "<p>14 g</p>"
    assert "tagline" not in after  # only the changed fields
    assert found[other_language()].before is None  # a first save has no before


@pytest.mark.django_db
def test_a_manager_save_that_changes_nothing_writes_no_entry() -> None:
    vendor = VendorFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    values = {"refund_policy": "<p>14 days</p>"}
    save_storefront_profile(vendor, primary_language(), values, actor=owner, ctx=CTX)
    save_storefront_profile(vendor, primary_language(), values, actor=owner, ctx=CTX)
    assert len(entries(vendor)) == 1


@pytest.mark.django_db
def test_the_helper_audits_a_change_a_delete_and_ignores_a_status_only_change() -> None:
    vendor = VendorFactory.create()
    row = VendorTranslation.objects.create(vendor=vendor, language=primary_language(), tagline="A")
    before = vendor_text_snapshot(vendor)
    row.status = VendorTranslation.Status.REVIEWED
    row.save()
    assert audit_vendor_text_changes(vendor, before, ctx=ADMIN_CTX).is_empty
    before = vendor_text_snapshot(vendor)
    row.tagline = "B"
    row.save()
    audit_vendor_text_changes(vendor, before, ctx=ADMIN_CTX)
    before = vendor_text_snapshot(vendor)
    row.delete()
    audit_vendor_text_changes(vendor, before, ctx=ADMIN_CTX)
    first, second = entries(vendor)
    assert (before_of(first)["tagline"], after_of(first)["tagline"]) == ("A", "B")
    assert before_of(second)["tagline"] == "B"
    assert second.after is None


@pytest.mark.django_db
def test_admin_inline_edits_in_two_languages_give_one_entry_each(
    client: Client, superuser: User, mfa_login: MfaLogin
) -> None:
    staff = mfa_login(client, superuser)
    add = {
        **vendor_form(),
        "translations-TOTAL_FORMS": "2",
        "translations-INITIAL_FORMS": "0",
        "translations-MIN_NUM_FORMS": "0",
        "translations-MAX_NUM_FORMS": "1000",
        **inline(0, primary_language(), refund_policy="14 days", tagline="Gear"),
        **inline(1, other_language(), refund_policy="14 giorni"),
    }
    response = staff.post(reverse("admin:vendors_vendor_add"), add)
    assert response.status_code == 302, response.content.decode()
    vendor = Vendor.objects.get()
    created = by_language(vendor)
    assert set(created) == {primary_language(), other_language()}
    assert created[other_language()].source == "admin"
    assert after_of(created[other_language()])["refund_policy"] == "14 giorni"

    rows = {row.language: row for row in vendor.translations.all()}
    change = {
        **vendor_form(slug=vendor.slug),
        "translations-TOTAL_FORMS": "2",
        "translations-INITIAL_FORMS": "2",
        "translations-MIN_NUM_FORMS": "0",
        "translations-MAX_NUM_FORMS": "1000",
        **inline(0, primary_language(), refund_policy="14 days", tagline="Gear"),
        **inline(1, other_language(), refund_policy="30 giorni"),
        "translations-0-id": str(rows[primary_language()].pk),
        "translations-1-id": str(rows[other_language()].pk),
        "translations-0-vendor": str(vendor.pk),
        "translations-1-vendor": str(vendor.pk),
    }
    response = staff.post(reverse("admin:vendors_vendor_change", args=[vendor.pk]), change)
    assert response.status_code == 302, response.content.decode()
    latest = entries(vendor)[-1]
    assert len(entries(vendor)) == 3  # the unchanged language wrote nothing
    assert after_of(latest)["language"] == other_language()
    assert (before_of(latest)["refund_policy"], after_of(latest)["refund_policy"]) == (
        "14 giorni",
        "30 giorni",
    )


# --- The guard: no door writes vendor texts without the audit

# File -> the name that proves it audits what it writes.
AUDITED_WRITERS = {
    "apps/vendors/services.py": "PROFILE_CHANGED",
    "apps/vendors/admin.py": "audit_vendor_text_changes",
    "apps/ingest/catalog_loader.py": "audit_vendor_text_changes",
    "apps/seed/services.py": "audit_vendor_text_changes",
}
WRITES = {"create", "update", "bulk_create", "bulk_update", "update_or_create", "get_or_create"}


def _writes_vendor_texts(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "VendorTranslation":
            return True
        if isinstance(func, ast.Attribute) and func.attr in WRITES:
            chain = ast.unparse(func.value)
            if "VendorTranslation" in chain or chain.endswith(".translations"):
                return True
    return False


def test_every_writer_of_vendor_texts_is_audited() -> None:
    root = Path(__file__).resolve().parents[4]
    strays = []
    for path in sorted((root / "apps").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if "/tests/" in relative or "/migrations/" in relative:
            continue
        source = path.read_text()
        if "VendorTranslation" not in source and ".translations" not in source:
            continue
        if not _writes_vendor_texts(ast.parse(source)):
            continue
        marker = AUDITED_WRITERS.get(relative)
        if marker is None or marker not in source:
            strays.append(relative)
    assert not strays, (
        f"{strays} write VendorTranslation texts without the VEN.39 audit: use "
        "vendors.services.save_storefront_profile, or take vendor_text_snapshot() before the "
        "write and call audit_vendor_text_changes() after it, then list the file here"
    )
