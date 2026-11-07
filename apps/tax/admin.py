"""Staff admin for the tax configuration (TAX.1; Wagtail admin replaces it in CORE.8).

Rates and vendor tax profiles are history: staff add a new row through the audited services,
which close the current one, and can view earlier rows but never edit or delete them.
Territories and legal notes are edited through audited services; VIES answers are view-only.

Regime change requests (TAX.2) come from vendor owners; staff review them here, next to the
vendor's regime history, and approve or reject them on review pages that need the review
permission and a fresh second factor (CORE.4 step-up). The changelists warn about pending
requests and about current profiles that disagree with the vendor's fiscal regime.
"""

from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from apps.accounts.decorators import step_up_required
from apps.accounts.models import User
from apps.core.audit import audit_context

from . import engine, services
from .models import (
    TaxRate,
    TaxRegimeChangeRequest,
    Territory,
    VatValidation,
    VendorTaxProfile,
    VendorTaxProfileTranslation,
)


class NoDeleteAdmin[M: Model](admin.ModelAdmin[M]):
    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class HistoryAdmin[M: Model](NoDeleteAdmin[M]):
    """Versioned rows: add only (through a service), view earlier rows."""

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class StartCheckedForm[M: Model](forms.ModelForm[M]):
    """Reports the service's start rule on `valid_from` before the service runs.

    `validate_unique` is the last step of ModelForm validation, once the instance is built and
    cleaned, so the rule sees normalized values (country codes, territory).
    """

    def check_start(self, instance: M) -> None:
        raise NotImplementedError

    def validate_unique(self) -> None:
        super().validate_unique()
        if self.instance.pk is not None or self.errors:
            return
        try:
            self.check_start(self.instance)
        except ValidationError as exc:
            self.add_error("valid_from", exc.message_dict["valid_from"])


class TaxRateForm(StartCheckedForm[TaxRate]):
    class Meta:
        model = TaxRate
        fields = ("country", "territory", "category", "rate", "source", "valid_from")

    def check_start(self, instance: TaxRate) -> None:
        services.check_rate_start(instance)


@admin.register(TaxRate)
class TaxRateAdmin(HistoryAdmin[TaxRate]):
    form = TaxRateForm
    list_display = ("country", "territory", "category", "rate", "source", "valid_from", "valid_to")
    list_filter = ("category", "source", "country")
    list_select_related = ("territory",)
    readonly_fields = ("public_id", "valid_to", "created_at")

    def save_model(self, request: HttpRequest, obj: TaxRate, form: Any, change: bool) -> None:
        services.configure_tax_rate(obj, ctx=audit_context(request))


@admin.register(Territory)
class TerritoryAdmin(NoDeleteAdmin[Territory]):
    """Rates point at territories (PROTECT), so they are changed, never deleted."""

    list_display = ("code", "name", "country", "vat_status", "postcode_patterns")
    list_filter = ("vat_status", "country")
    search_fields = ("code", "name")
    readonly_fields = ("public_id", "created_at", "updated_at")

    def save_model(self, request: HttpRequest, obj: Territory, form: Any, change: bool) -> None:
        services.save_territory(obj, ctx=audit_context(request))


class VendorTaxProfileForm(StartCheckedForm[VendorTaxProfile]):
    class Meta:
        model = VendorTaxProfile
        fields = (
            "vendor",
            "regime",
            "exempt_reason",
            "oss_registered_since",
            "requested_by",
            "valid_from",
        )

    def check_start(self, instance: VendorTaxProfile) -> None:
        services.check_profile_start(instance)


def warn_about_tax_profiles(modeladmin: admin.ModelAdmin[Any], request: HttpRequest) -> None:
    """The legacy review notices: pending requests, and profiles out of line with VEN.12."""
    pending = services.pending_regime_changes().count()
    if pending:
        noun = "request is" if pending == 1 else "requests are"
        modeladmin.message_user(
            request, f"{pending} tax regime change {noun} waiting for review.", messages.WARNING
        )
    for item in services.inconsistent_tax_profiles():
        modeladmin.message_user(request, item.describe(), messages.WARNING)


@admin.register(VendorTaxProfile)
class VendorTaxProfileAdmin(HistoryAdmin[VendorTaxProfile]):
    """Adding a profile here approves it in the staff member's name; vendor requests are
    reviewed under tax regime change requests (TAX.2)."""

    form = VendorTaxProfileForm
    list_display = ("vendor", "regime", "exempt_reason", "valid_from", "valid_to", "approved_by")
    list_filter = ("regime", "exempt_reason")
    search_fields = ("vendor__display_name", "vendor__legal_name")
    list_select_related = ("vendor", "approved_by")
    raw_id_fields = ("vendor", "requested_by")
    readonly_fields = ("public_id", "valid_to", "approved_by", "created_at")

    def save_model(
        self, request: HttpRequest, obj: VendorTaxProfile, form: Any, change: bool
    ) -> None:
        obj.approved_by = request.user  # type: ignore[assignment]
        services.configure_vendor_tax_profile(obj, ctx=audit_context(request))

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        warn_about_tax_profiles(self, request)
        return super().changelist_view(request, extra_context)


@admin.register(VendorTaxProfileTranslation)
class VendorTaxProfileTranslationAdmin(NoDeleteAdmin[VendorTaxProfileTranslation]):
    """Legal notes per language; the profile version they belong to never changes."""

    list_display = ("profile", "language", "status")
    list_filter = ("language", "status")
    search_fields = ("profile__vendor__display_name", "legal_note")
    list_select_related = ("profile__vendor",)
    raw_id_fields = ("profile",)
    readonly_fields = ("public_id", "source_hash", "created_at", "updated_at")

    def save_model(
        self, request: HttpRequest, obj: VendorTaxProfileTranslation, form: Any, change: bool
    ) -> None:
        services.save_legal_note(obj, ctx=audit_context(request))


@admin.register(VatValidation)
class VatValidationAdmin(HistoryAdmin[VatValidation]):
    """Answers are recorded by the VIES client (TAX.5); staff only look at them."""

    list_display = ("checked_at", "country", "vat_number", "result", "name")
    list_filter = ("result", "country")
    search_fields = ("vat_number", "name")
    readonly_fields = (
        "public_id",
        "country",
        "vat_number",
        "result",
        "name",
        "address",
        "request_id",
        "checked_at",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


# --- Regime change requests (TAX.2)

REVIEW_TEMPLATE = "admin/tax/taxregimechangerequest/review.html"


class ApproveForm(forms.Form):
    reason = forms.CharField(
        label="Reason",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Optional: what was checked. The vendor can read it.",
    )


class RejectForm(forms.Form):
    reason = forms.CharField(
        label="Reason",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Required: why the request is rejected. The vendor can read it.",
    )


def _when(value: Any) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else "-"


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]], empty: str) -> SafeString | str:
    if not rows:
        return empty
    body = format_html_join(
        "",
        "<tr>{}</tr>",
        ((format_html_join("", "<td>{}</td>", ((cell,) for cell in row)),) for row in rows),
    )
    return format_html(
        "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>",
        format_html_join("", '<th scope="col">{}</th>', ((header,) for header in headers)),
        body,
    )


def _regime_label(regime: str, exempt_reason: str) -> str:
    label = str(VendorTaxProfile.Regime(regime).label)
    if exempt_reason:
        label += f": {VendorTaxProfile.ExemptReason(exempt_reason).label}"
    return label


@admin.register(TaxRegimeChangeRequest)
class TaxRegimeChangeRequestAdmin(HistoryAdmin[TaxRegimeChangeRequest]):
    """Requests come from vendor owners and are reviewed on their own pages; never edited."""

    list_display = (
        "vendor",
        "status",
        "regime",
        "exempt_reason",
        "fiscal_regime",
        "effective_from",
        "requested_by",
        "requested_at",
        "reviewed_by",
    )
    list_filter = ("status", "regime", "exempt_reason")
    search_fields = ("vendor__display_name", "vendor__legal_name")
    list_select_related = ("vendor", "requested_by", "reviewed_by")
    request_fields = (
        "regime",
        "exempt_reason",
        "oss_registered_since",
        "fiscal_regime",
        "effective_from",
        "note",
        "document_reference",
        "requested_by",
        "requested_at",
    )
    review_fields = ("reviewed_by", "reviewed_at", "review_reason", "profile")
    history_fields = ("current_regime", "profile_history", "request_history")
    fieldsets = (
        (None, {"fields": ("vendor", "status", "review")}),
        ("Requested regime", {"fields": request_fields}),
        ("Review", {"fields": review_fields}),
        ("Vendor history", {"fields": history_fields}),
    )
    readonly_fields = (
        "vendor",
        "status",
        "review",
        *request_fields,
        *review_fields,
        *history_fields,
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        warn_about_tax_profiles(self, request)
        return super().changelist_view(request, extra_context)

    # --- Read-only panels

    @admin.display(description="Review")
    def review(self, obj: TaxRegimeChangeRequest) -> SafeString | str:
        if obj.status != engine.PENDING:
            return str(obj.get_status_display())
        return format_html(
            '<a class="button" href="{}">Approve</a> <a class="button" href="{}">Reject</a>',
            reverse("admin:tax_taxregimechangerequest_approve", args=[obj.pk]),
            reverse("admin:tax_taxregimechangerequest_reject", args=[obj.pk]),
        )

    @admin.display(description="Current regime")
    def current_regime(self, obj: TaxRegimeChangeRequest) -> str:
        try:
            profile = services.current_tax_profile(obj.vendor)
        except services.NoVendorTaxProfile:
            regime = "No tax profile yet"
        else:
            regime = _regime_label(profile.regime, profile.exempt_reason)
        return f"{regime}. Fiscal regime: {obj.vendor.fiscal_regime or 'not declared'}."

    @admin.display(description="Tax profile versions")
    def profile_history(self, obj: TaxRegimeChangeRequest) -> SafeString | str:
        rows = [
            (
                _when(profile.valid_from),
                _when(profile.valid_to),
                _regime_label(profile.regime, profile.exempt_reason),
                profile.approved_by or "-",
            )
            for profile in services.regime_history(obj.vendor).profiles
        ]
        return _table(("From", "To", "VAT regime", "Approved by"), rows, "No tax profile yet.")

    @admin.display(description="Requests of this vendor")
    def request_history(self, obj: TaxRegimeChangeRequest) -> SafeString | str:
        rows = [
            (
                _when(item.requested_at),
                _regime_label(item.regime, item.exempt_reason),
                _when(item.effective_from),
                item.get_status_display(),
                item.reviewed_by or "-",
                item.review_reason or "-",
            )
            for item in services.regime_history(obj.vendor).requests
            if item.pk != obj.pk
        ]
        return _table(
            ("Requested", "VAT regime", "From", "Status", "Reviewed by", "Reason"),
            rows,
            "No other requests.",
        )

    # --- Review pages: the review permission and a fresh second factor (CORE.4)

    def get_urls(self) -> list[URLPattern]:
        prefix = f"{self.opts.app_label}_{self.opts.model_name}"
        review = [
            path(
                "<path:object_id>/approve/",
                self.admin_site.admin_view(step_up_required()(self.approve_view)),
                name=f"{prefix}_approve",
            ),
            path(
                "<path:object_id>/reject/",
                self.admin_site.admin_view(step_up_required()(self.reject_view)),
                name=f"{prefix}_reject",
            ),
        ]
        return review + super().get_urls()

    def approve_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        return self._review_view(request, object_id, approve=True)

    def reject_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        return self._review_view(request, object_id, approve=False)

    def _review_view(self, request: HttpRequest, object_id: str, *, approve: bool) -> HttpResponse:
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404("No such tax regime change request.")
        if not request.user.has_perm(services.REVIEW_PERMISSION):  # the services check it too
            raise PermissionDenied
        change_url = reverse("admin:tax_taxregimechangerequest_change", args=[obj.pk])
        if obj.status != engine.PENDING:
            status = str(obj.get_status_display()).lower()
            self.message_user(request, f"This request is already {status}.", messages.WARNING)
            return redirect(change_url)

        form: forms.Form = (ApproveForm if approve else RejectForm)(request.POST or None)
        if request.method == "POST" and form.is_valid():
            reviewer = cast(User, request.user)
            reason: str = form.cleaned_data["reason"]
            ctx = audit_context(request)
            try:
                if approve:
                    services.approve_regime_change(obj, reviewer, reason=reason, ctx=ctx)
                else:
                    services.reject_regime_change(obj, reviewer, reason=reason, ctx=ctx)
            except ValidationError as exc:
                form.add_error(None, list(exc.messages))
            except engine.RegimeChangeError as exc:
                form.add_error(None, str(exc))
            else:
                done = "approved" if approve else "rejected"
                self.message_user(request, f"The request was {done}.", messages.SUCCESS)
                return redirect(change_url)

        action = "Approve" if approve else "Reject"
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "original": obj,
            "title": f"{action} the tax regime change of {obj.vendor}",
            "action": action,
            "form": form,
            "change_url": change_url,
            "summary": [
                ("Vendor", obj.vendor),
                ("Current regime", self.current_regime(obj)),
                ("Requested regime", _regime_label(obj.regime, obj.exempt_reason)),
                ("Fiscal regime to declare", obj.fiscal_regime or "Unchanged"),
                ("From", _when(obj.effective_from)),
                ("Note", obj.note or "-"),
                ("Supporting document", obj.document_reference or "-"),
                ("Requested by", obj.requested_by),
            ],
        }
        return TemplateResponse(request, REVIEW_TEMPLATE, context)
