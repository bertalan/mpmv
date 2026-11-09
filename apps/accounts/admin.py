"""Staff admin for users and groups (Wagtail admin replaces this in CORE.8).

Every write goes through the audited services (SEC.4): profile and flags, groups and
permissions, passwords set by staff, deletions. A staff member's own password change is
audited by the site itself (`admin_site.AuditedAdminSite`).

Privilege rules (audit #98 chunk 3 M1; strict reading until CORE.8, spec 02 keeps this admin
for the technical superuser): only a superuser grants superuser or staff status, groups or
permissions, and uses the group admin. A privileged account (staff, superuser, or one that holds
a group or a direct permission, such as a Wagtail editor) is managed by superusers only: setting
its password, resetting its MFA, changing its email or active flag, and deleting it. The user
change form, the password form and deletion need a fresh step-up (CORE.4).
"""

from typing import Any, ClassVar

from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected as django_delete_selected
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.audit import audit_context

from . import services
from .decorators import step_up_required
from .mfa_session import is_recently_verified
from .models import User
from .services import has_second_factor, is_privileged, reset_mfa

# Second factors are enrolled only through the audited MFA pages (CORE.4): the raw device admin
# would let staff create or read authenticator secrets for other users.
if admin.site.is_registered(TOTPDevice):
    admin.site.unregister(TOTPDevice)

# Replaced by the audited GroupAdmin below.
if admin.site.is_registered(Group):
    admin.site.unregister(Group)


class AuditedPasswordChangeForm(AdminPasswordChangeForm[User]):
    """Remembers whether the user had a usable password before the change, for the audit."""

    def __init__(self, user: User, *args: Any, **kwargs: Any) -> None:
        self.had_usable_password = user.has_usable_password()
        super().__init__(user, *args, **kwargs)


# Fields that grant access: only a superuser may change them.
ACCESS_FIELDS = ("is_staff", "is_superuser", "groups", "user_permissions")

# Identity fields of a privileged account: only a superuser may change them (review of #111).
IDENTITY_FIELDS = ("email", "is_active")

# An account that carries access in any form. A group counts even without permissions today:
# a superuser can add some to it at any time. The joins can repeat rows, so use `.exists()`.
PRIVILEGED = (
    Q(is_staff=True)
    | Q(is_superuser=True)
    | Q(groups__isnull=False)
    | Q(user_permissions__isnull=False)
)


def _is_superuser(request: HttpRequest) -> bool:
    return bool(getattr(request.user, "is_superuser", False))


def may_manage_account(request: HttpRequest, target: User) -> bool:
    """Whether the actor may take over or remove `target`.

    Covers the password, MFA, email and active flag, and deletion. Privileged accounts are
    managed by superusers only (the actor's own one too: they change their own password
    through the site's password-change form, which asks for the old one). Taking over a
    privileged account would give the actor permissions they do not hold.
    """
    return _is_superuser(request) or not is_privileged(target)


def _refuses_batch(request: HttpRequest, queryset: QuerySet[User]) -> bool:
    return not _is_superuser(request) and queryset.filter(PRIVILEGED).exists()


# The admin login renders the Turnstile widget when the captcha is enabled (CORE.5).
admin.site.login_template = "accounts/admin/login.html"


@admin.register(User)
class UserAdmin(DjangoUserAdmin[User]):
    ordering = ("email",)
    list_display = ("email", "full_name", "is_staff", "is_active", "preferred_language", "mfa")
    search_fields = ("email", "full_name")
    readonly_fields = ("public_id", "date_joined", "last_login")
    actions = ("reset_mfa", "delete_selected")
    fieldsets = (
        (None, {"fields": ("email", "password", "public_id")}),
        ("Profile", {"fields": ("full_name", "preferred_language")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    filter_horizontal: ClassVar[tuple[str, ...]] = ("groups", "user_permissions")

    @admin.display(boolean=True, description="MFA")
    def mfa(self, obj: User) -> bool:
        return has_second_factor(obj)

    @admin.action(
        description="Reset two-factor authentication (user must enrol again)",
        permissions=["change"],
    )
    def reset_mfa(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        if not is_recently_verified(request):
            self.message_user(
                request,
                "Verify your own second factor again before resetting someone else's.",
                messages.ERROR,
            )
            return
        if _refuses_batch(request, queryset):
            self.message_user(
                request,
                "Only a superuser can reset two-factor authentication for accounts that hold "
                "access (staff, groups or permissions).",
                messages.ERROR,
            )
            return
        ctx = audit_context(request)
        for user in queryset:
            reset_mfa(user, ctx=ctx)
        self.message_user(request, f"Two-factor authentication reset for {queryset.count()}.")

    @admin.action(
        description=gettext_lazy("Delete selected %(verbose_name_plural)s"),
        permissions=["delete"],
    )
    def delete_selected(self, request: HttpRequest, queryset: QuerySet[User]) -> Any:
        """Django's bulk delete behind step-up, refused as a whole for privileged accounts."""
        if not is_recently_verified(request):
            self.message_user(
                request,
                "Verify your own second factor again before deleting users.",
                messages.ERROR,
            )
            return None
        if _refuses_batch(request, queryset):
            self.message_user(
                request,
                "Only a superuser can delete accounts that hold access (staff, groups or "
                "permissions).",
                messages.ERROR,
            )
            return None
        return django_delete_selected(self, request, queryset)

    # --- Privilege rules (audit #98 chunk 3 M1)

    def get_readonly_fields(self, request: HttpRequest, obj: User | None = None) -> Any:
        fields = tuple(super().get_readonly_fields(request, obj))
        if _is_superuser(request):
            return fields
        locked: tuple[str, ...] = ACCESS_FIELDS
        if obj is not None and is_privileged(obj):
            locked += IDENTITY_FIELDS
        return fields + tuple(name for name in locked if name not in fields)

    def has_delete_permission(self, request: HttpRequest, obj: User | None = None) -> bool:
        if obj is not None and not may_manage_account(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        parent = super().change_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, form_url, extra_context)

        return step_up_required()(view)(request)

    def user_change_password(
        self, request: HttpRequest, id: str, form_url: str = ""
    ) -> HttpResponse:
        parent = super().user_change_password

        def view(req: HttpRequest) -> HttpResponse:
            target = self.get_object(req, unquote(id))
            if target is not None and not may_manage_account(req, target):
                raise PermissionDenied
            return parent(req, id, form_url)

        return step_up_required()(view)(request)

    def delete_view(
        self, request: HttpRequest, object_id: str, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().delete_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, extra_context)

        return step_up_required()(view)(request)

    # --- Audited writes (SEC.4)

    change_password_form = AuditedPasswordChangeForm

    def save_model(self, request: HttpRequest, obj: User, form: Any, change: bool) -> None:
        services.save_user(obj, ctx=audit_context(request))

    def save_related(self, request: HttpRequest, form: Any, formsets: Any, change: bool) -> None:
        before = services.access_snapshot(form.instance)
        super().save_related(request, form, formsets, change)
        services.record_access_change(form.instance, before, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: User) -> None:
        # Second line of defence: `has_delete_permission` already refuses this.
        if not may_manage_account(request, obj):
            raise PermissionDenied
        services.delete_user(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        # Second line of defence: the `delete_selected` action already refuses this.
        if not is_recently_verified(request) or _refuses_batch(request, queryset):
            raise PermissionDenied
        ctx = audit_context(request)
        for user in queryset:
            services.delete_user(user, ctx=ctx)

    def construct_change_message(
        self, request: HttpRequest, form: Any, formsets: Any, add: bool = False
    ) -> list[dict[str, Any]]:
        # Called by `user_change_password` right after the form set the password: the only
        # hook of that view that has both the request and the saved form.
        if isinstance(form, AuditedPasswordChangeForm):
            services.record_password_change(
                form.user, had_usable_password=form.had_usable_password, ctx=audit_context(request)
            )
        return super().construct_change_message(request, form, formsets, add)


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    """Django's group admin, with every write audited (a group grants permissions).

    Superusers only, whatever model permissions the actor holds: `change_group` would otherwise
    let staff add any permission to a group they belong to. Letting staff grant only the
    permissions they already hold would also need rules for removals, for every other member of
    the group and for permissions that change later; superuser-only is the smaller and safer
    rule until CORE.8.
    """

    def has_module_permission(self, request: HttpRequest) -> bool:
        return _is_superuser(request)

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return _is_superuser(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return _is_superuser(request)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return _is_superuser(request)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return _is_superuser(request)

    def save_model(self, request: HttpRequest, obj: Group, form: Any, change: bool) -> None:
        services.save_group(obj, ctx=audit_context(request))

    def save_related(self, request: HttpRequest, form: Any, formsets: Any, change: bool) -> None:
        before = services.group_access_snapshot(form.instance)
        super().save_related(request, form, formsets, change)
        services.record_group_access_change(form.instance, before, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: Group) -> None:
        services.delete_group(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Group]) -> None:
        ctx = audit_context(request)
        for group in queryset:
            services.delete_group(group, ctx=ctx)
