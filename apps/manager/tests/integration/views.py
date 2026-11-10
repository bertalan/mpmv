"""Test-only Manager sections (CORE.17): a member list with filters, pagination and bulk actions,
an owner-only page, and views that break the rules, mounted by `urls.py` in these tests only."""

from typing import Any, ClassVar

from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.manager.lists import BulkAction, Column, ListFilter, ManagerListView
from apps.manager.navigation import Section, anyone, vendor_action
from apps.manager.spaces import Space
from apps.manager.views import ManagerView
from apps.vendors.models import VendorMember

# Every call of the bulk actions below: (action, member public ids, dry_run).
CALLS: list[tuple[str, list[str], bool]] = []


def _touch(name: str) -> Any:
    def run(objects: list[VendorMember], *, ctx: AuditContext, dry_run: bool) -> ChangeSet:
        keys = [str(obj.public_id) for obj in objects]
        CALLS.append((name, keys, dry_run))
        changes = ChangeSet()
        for key in keys:
            changes.update("vendors.vendormember", key, {"touched": False}, {"touched": True})
        return changes

    return run


class MemberList(ManagerListView):
    section = "test.members"
    title = "Members"
    paginate_by = 2
    columns = (
        Column("Email", lambda member: member.user.email),
        Column("Role", lambda member: member.role),
    )
    filters = (ListFilter("role", "Role", (("owner", "Owner"), ("staff", "Staff")), lookup="role"),)
    bulk_actions = (
        BulkAction("touch", "Touch", _touch("touch"), allowed=anyone),
        BulkAction("sensitive", "Sensitive", _touch("sensitive"), step_up=True, allowed=anyone),
        BulkAction(
            "owners_only", "Owners only", _touch("owners_only"), allowed=vendor_action("members")
        ),
    )

    def get_queryset(self) -> QuerySet[VendorMember]:
        return self.scoped(VendorMember).select_related("user").order_by("user__email")


class UnscopedMemberList(MemberList):
    """A list that forgets `scoped()`: the base class must still keep other vendors' rows out."""

    section = "test.unscoped_members"

    def get_queryset(self) -> QuerySet[VendorMember]:
        return VendorMember.objects.select_related("user").order_by("user__email")


class OwnerOnly(ManagerView):
    section = "test.owner_only"
    template_name = "manager/shop/dashboard.html"


class PlatformSectionOnShop(ManagerView):
    section = "platform.dashboard"
    template_name = "manager/platform/dashboard.html"


def no_section(request: HttpRequest) -> HttpResponse:
    return HttpResponse("this view declares no section")


class UnknownSection(ManagerView):
    section: ClassVar[str] = "test.unregistered"
    template_name = "manager/shop/dashboard.html"


TEST_SECTIONS = (
    Section("test.members", Space.SHOP, "Test members", "test_members", order=900, allowed=anyone),
    Section(
        "test.unscoped_members",
        Space.SHOP,
        "Test unscoped members",
        "test_unscoped_members",
        order=905,
        allowed=anyone,
    ),
    Section(
        "test.owner_only",
        Space.SHOP,
        "Test owner only",
        "test_owner_only",
        order=910,
        allowed=vendor_action("payouts"),
    ),
)
