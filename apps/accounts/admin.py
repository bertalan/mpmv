"""Staff admin for users (Wagtail admin replaces this in CORE.8)."""

from typing import ClassVar

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import QuerySet
from django.http import HttpRequest
from django_otp.plugins.otp_totp.models import TOTPDevice

from .http import audit_context
from .mfa_session import is_recently_verified
from .models import User
from .services import has_second_factor, reset_mfa

# Second factors are enrolled only through the audited MFA pages (CORE.4): the raw device admin
# would let staff create or read authenticator secrets for other users.
if admin.site.is_registered(TOTPDevice):
    admin.site.unregister(TOTPDevice)

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
