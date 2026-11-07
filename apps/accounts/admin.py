"""Staff admin for users and groups (Wagtail admin replaces this in CORE.8).

Every write goes through the audited services (SEC.4): profile and flags, groups and
permissions, passwords set by staff, deletions.
"""

from typing import Any, ClassVar

from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from django.http import HttpRequest
from django_otp.plugins.otp_totp.models import TOTPDevice

from . import services
from .http import audit_context
from .mfa_session import is_recently_verified
from .models import User
from .services import has_second_factor, reset_mfa

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


# The admin login renders the Turnstile widget when the captcha is enabled (CORE.5).
admin.site.login_template = "accounts/admin/login.html"


@admin.register(User)
class UserAdmin(DjangoUserAdmin[User]):
    ordering = ("email",)
    list_display = ("email", "full_name", "is_staff", "is_active", "preferred_language", "mfa")
    search_fields = ("email", "full_name")
    readonly_fields = ("public_id", "date_joined", "last_login")
    actions = ("reset_mfa",)
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
        ctx = audit_context(request)
        for user in queryset:
            reset_mfa(user, ctx=ctx)
        self.message_user(request, f"Two-factor authentication reset for {queryset.count()}.")

    # --- Audited writes (SEC.4)

    change_password_form = AuditedPasswordChangeForm

    def save_model(self, request: HttpRequest, obj: User, form: Any, change: bool) -> None:
        services.save_user(obj, ctx=audit_context(request))

    def save_related(self, request: HttpRequest, form: Any, formsets: Any, change: bool) -> None:
        before = services.access_snapshot(form.instance)
        super().save_related(request, form, formsets, change)
        services.record_access_change(form.instance, before, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: User) -> None:
        services.delete_user(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
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
    """Django's group admin, with every write audited (a group grants permissions)."""

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
