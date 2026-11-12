"""Staff choose each Wagtail Site's theme (THM.1). Every save goes through the audited service;
rows are never deleted: "Environment default" stands for no choice."""

from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.core.audit import audit_context

from .models import ThemeSettings
from .services import get_registry, set_site_theme

DEFAULT_CHOICE = ("", "Environment default (MPMV_THEME_DEFAULT)")


def theme_choices() -> list[tuple[str, str]]:
    return [DEFAULT_CHOICE, *get_registry().choices()]


class ThemeSettingsForm(forms.ModelForm[ThemeSettings]):
    # Read from the registry when the form is built: adding a theme needs no migration.
    theme = forms.ChoiceField(
        choices=theme_choices,
        required=False,
        label="Theme",
        help_text=_(
            "The name of an installed theme. Leave it empty to use the default theme of the "
            "environment."
        ),
    )

    class Meta:
        model = ThemeSettings
        fields = ("site", "theme")


@admin.register(ThemeSettings)
class ThemeSettingsAdmin(admin.ModelAdmin[ThemeSettings]):
    form = ThemeSettingsForm
    list_display = ("site", "theme_name")
    list_select_related = ("site",)

    @admin.display(description="Theme")
    def theme_name(self, obj: ThemeSettings) -> str:
        if not obj.theme:
            return DEFAULT_CHOICE[1]
        registry = get_registry()
        return registry[obj.theme].name if obj.theme in registry else f"{obj.theme} (unknown)"

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return ("site",) if obj is not None else ()

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(self, request: HttpRequest, obj: ThemeSettings, form: Any, change: bool) -> None:
        set_site_theme(obj.site, obj.theme, ctx=audit_context(request))
        # The service saved its own instance: point the admin's at the stored row.
        obj.pk = ThemeSettings.objects.values_list("pk", flat=True).get(site=obj.site)
        obj._state.adding = False
