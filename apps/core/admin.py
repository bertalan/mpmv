"""Staff admin for the platform operator (Wagtail admin replaces this in CORE.8).

Configuration rows are history: staff add a new row (closing the current one through the audited
service) and can view earlier rows, but never edit or delete them.
"""

import datetime as dt
from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest

from .audit import AuditContext
from .http import client_ip
from .models import AuditLog, PlatformOperator
from .services import check_operator_start, configure_platform_operator, latest_operator


class PlatformOperatorForm(forms.ModelForm[PlatformOperator]):
    class Meta:
        model = PlatformOperator
        fields = (
            "legal_name",
            "country",
            "vat_number",
            "tax_regime",
            "commission_vat_mode",
            "einvoice_channel",
            "dac7_authority_country",
            "valid_from",
        )

    def clean_valid_from(self) -> dt.datetime:
        valid_from: dt.datetime = self.cleaned_data["valid_from"]
        if self.instance.pk is None:
            try:
                check_operator_start(valid_from, latest_operator())
            except forms.ValidationError as exc:
                raise forms.ValidationError(exc.message_dict["valid_from"]) from exc
        return valid_from


@admin.register(PlatformOperator)
class PlatformOperatorAdmin(admin.ModelAdmin[PlatformOperator]):
    form = PlatformOperatorForm
    list_display = (
        "legal_name",
        "country",
        "vat_number",
        "commission_vat_mode",
        "einvoice_channel",
        "valid_from",
        "valid_to",
    )
    readonly_fields = ("public_id", "valid_to", "created_at")

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(
        self, request: HttpRequest, obj: PlatformOperator, form: Any, change: bool
    ) -> None:
        ctx = AuditContext(
            actor_id=request.user.pk,
            actor_label=f"staff:{request.user.get_username()}",
            source=AuditLog.Source.ADMIN,
            ip=client_ip(request),
        )
        configure_platform_operator(obj, ctx=ctx)
