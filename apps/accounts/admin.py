"""Staff admin for users (Wagtail admin replaces this in CORE.8)."""

from typing import ClassVar

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin[User]):
    ordering = ("email",)
    list_display = ("email", "full_name", "is_staff", "is_active", "preferred_language")
    search_fields = ("email", "full_name")
    readonly_fields = ("public_id", "date_joined", "last_login")
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
