"""Admin of the legacy redirects (CAT.6, spec 03 §3.1-3.2).

A thin adapter: the form reports what `services.validate_redirect` refuses (an off-site
target, a loop, a chain), and every write goes through the audited `services.save_redirect` and
`services.delete_redirect`. Whole maps are loaded with `manage.py import_redirects`.
"""

from typing import Any

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.core.audit import audit_context

from . import engine, services
from .models import Redirect

TIMESTAMPS = ("public_id", "created_at", "updated_at")


class RedirectForm(forms.ModelForm[Redirect]):
    class Meta:
        model = Redirect
        fields = (
            "source_path",
            "match_prefix",
            "code",
            "target_kind",
            "target_path",
            "target_product",
            "target_term",
            "target_query",
            "origin",
            "expires_at",
            "note",
        )

    def clean(self) -> dict[str, Any]:
        """Loops and chains; the rule's own shape is checked by `Redirect.clean`."""
        cleaned = super().clean() or {}
        candidate = Redirect(
            pk=self.instance.pk,
            source_path=engine.normalise_redirect_source(cleaned.get("source_path") or ""),
            match_prefix=bool(cleaned.get("match_prefix")),
            code=cleaned.get("code") or Redirect.Code.MOVED,
            target_kind=cleaned.get("target_kind") or "",
            target_path=cleaned.get("target_path") or "",
            expires_at=cleaned.get("expires_at"),
        )
        problems = services.redirect_problems([candidate])
        if problems:
            raise ValidationError(list(problems.values()))
        return cleaned


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin[Redirect]):
    form = RedirectForm
    list_display = ("source_path", "match_prefix", "code", "target_kind", "origin", "expires_at")
    list_filter = ("origin", "code", "target_kind", "match_prefix")
    search_fields = ("source_path", "target_path", "target_query", "note")
    autocomplete_fields = ("target_product", "target_term")
    readonly_fields = TIMESTAMPS
    fieldsets = (
        (None, {"fields": ("source_path", "match_prefix", "code", "origin", "expires_at", "note")}),
        (
            "Target (301 only)",
            {
                "fields": (
                    "target_kind",
                    "target_path",
                    "target_product",
                    "target_term",
                    "target_query",
                )
            },
        ),
        ("Record", {"fields": TIMESTAMPS}),
    )

    def save_model(
        self, request: HttpRequest, obj: Redirect, form: forms.ModelForm[Redirect], change: bool
    ) -> None:
        services.save_redirect(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: Redirect) -> None:
        services.delete_redirect(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Redirect]) -> None:
        ctx = audit_context(request)
        for redirect in queryset:
            services.delete_redirect(redirect, ctx=ctx)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Redirect]:
        return super().get_queryset(request).select_related("target_product", "target_term")
