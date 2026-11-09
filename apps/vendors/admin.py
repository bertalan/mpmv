"""Staff admin for vendors, their translations and the legal field requirements
(Wagtail admin replaces this in CORE.8).

Vendor writes go through the audited services (SEC.4); the change page links to the vendor's
audit trail.
"""

from collections.abc import Callable
from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.http import urlencode
from django.utils.safestring import SafeString

from apps.accounts.mfa_session import is_recently_verified
from apps.accounts.models import User
from apps.core.admin import step_up
from apps.core.audit import audit_context
from apps.core.changeset import ChangeSet

from . import engine
from .models import (
    ContractAcceptance,
    CrossVendorConsent,
    LegalFieldRequirement,
    Vendor,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorStatus,
    VendorTranslation,
    legal_field_label,
)
from .services import (
    can_reopen,
    change_status,
    check_contract_start,
    correct_dac7_dataset,
    delete_requirement,
    delete_vendor,
    delete_vendor_flag,
    establishment_flags,
    establishment_warning,
    legal_profile_completeness,
    onboarding_checklist,
    reopen_vendor,
    save_contract,
    save_requirement,
    save_vendor,
    save_vendor_flag,
    set_cross_vendor_consent,
)

StatusAction = Callable[[admin.ModelAdmin[Vendor], HttpRequest, QuerySet[Vendor]], None]


def warn_about_establishment(
    modeladmin: admin.ModelAdmin[Vendor], request: HttpRequest, vendor: Vendor, changes: ChangeSet
) -> None:
    """Warn staff when a save or a reinstatement leaves `vendor` outside the allowed vendor
    countries (CMP.13): staff decide, nobody is suspended automatically."""
    for change in establishment_flags(changes):
        warning = establishment_warning(change.after or {}, vendor.status)
        modeladmin.message_user(request, f"{vendor}: {warning}", messages.WARNING)


def status_action(target: VendorStatus) -> StatusAction:
    """An admin action that moves the selected vendors to `target` through the service."""

    def action(
        modeladmin: admin.ModelAdmin[Vendor], request: HttpRequest, queryset: QuerySet[Vendor]
    ) -> None:
        ctx = audit_context(request)
        moved = 0
        for vendor in queryset:
            try:
                changes = change_status(vendor, target, ctx=ctx)
            except engine.InvalidTransition as exc:
                modeladmin.message_user(request, f"{vendor}: {exc}", messages.ERROR)
            else:
                moved += 1
                warn_about_establishment(modeladmin, request, vendor, changes)
        if moved:
            modeladmin.message_user(
                request, f"{moved} vendor(s) moved to {target.label.lower()}.", messages.SUCCESS
            )

    action.__name__ = f"move_to_{target.value}"
    # Without `permissions` Django offers the action to anyone who can view the changelist.
    return admin.action(
        description=f"Move selected vendors to {target.label.lower()}", permissions=["change"]
    )(action)


class VendorTranslationInline(admin.StackedInline[VendorTranslation, Vendor]):
    model = VendorTranslation
    extra = 1
    fields = (
        "language",
        "status",
        "tagline",
        "description",
        "shipping_policy",
        "refund_policy",
        "cancellation_policy",
    )


REOPEN_TEMPLATE = "admin/vendors/vendor/reopen.html"


class ReopenForm(forms.Form):
    reason = forms.CharField(
        label="Reason",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Required: why the closed vendor is reopened. It is kept in the audit log.",
    )


def _labels(names: tuple[str, ...]) -> str:
    return ", ".join(legal_field_label(name) for name in names)


def _field_labels(names: tuple[str, ...]) -> str:
    """Labels of vendor fields; other names (e.g. "vat_number or tax_code") as they are."""
    labels = []
    for name in names:
        try:
            labels.append(legal_field_label(name))
        except FieldDoesNotExist:
            labels.append(name.replace("_", " "))
    return ", ".join(labels)


def _address(prefix: str) -> tuple[str, ...]:
    return tuple(
        f"{prefix}_{name}"
        for name in ("street", "street_number", "postal_code", "city", "province", "country")
    )


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin[Vendor]):
    list_display = ("display_name", "legal_name", "status", "country", "contact_email")
    list_filter = ("status", "country", "sales_mode_default", "legal_form")
    search_fields = ("display_name", "legal_name", "slug", "vat_number", "contact_email")
    readonly_fields = (
        "status",
        "public_id",
        "audit_trail",
        "onboarding",
        "onboarding_round",
        "reopen",
        "missing_for_payout",
        "vies_status",
        "vies_checked_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("display_name", "slug", "status", "public_id", "audit_trail")}),
        ("Onboarding", {"fields": ("onboarding", "onboarding_round", "reopen")}),
        ("Payout readiness", {"fields": ("missing_for_payout",)}),
        (
            "Legal identity",
            {
                "fields": (
                    "legal_name",
                    "country",
                    "legal_form",
                    "first_name",
                    "last_name",
                    "date_of_birth",
                    "place_of_birth",
                )
            },
        ),
        ("Registered office", {"fields": _address("registered")}),
        (
            "Tax identity",
            {
                "fields": (
                    "vat_number",
                    "vies_status",
                    "vies_checked_at",
                    "tax_code",
                    "tin_country",
                    "tin_same_as_vat",
                    "tax_residence_country",
                    "fiscal_regime",
                    "cross_border_franchise_number",
                    "ateco_code",
                    "eori",
                )
            },
        ),
        (
            "Company register (REA)",
            {
                "fields": (
                    "business_registration_number",
                    "rea_office",
                    "rea_number",
                    "share_capital",
                    "sole_shareholder",
                    "liquidation_status",
                )
            },
        ),
        ("E-invoicing", {"fields": ("sdi_recipient_code", "pec")}),
        (
            "Fiscal representative (non-EU vendors)",
            {
                "classes": ("collapse",),
                "fields": ("fiscal_representative_name", "fiscal_representative_vat_number"),
            },
        ),
        (
            "Permanent establishment",
            {"classes": ("collapse",), "fields": _address("establishment")},
        ),
        (
            "Invoicing bank details",
            {"fields": ("iban", "bic", "bank_name", "account_holder_name")},
        ),
        ("Contact", {"fields": ("contact_email", "phone")}),
        ("Sales", {"fields": ("sales_mode_default", "enquiries_enabled")}),
        ("Migration", {"fields": ("legacy_wp_user_id",)}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (VendorTranslationInline,)
    # One action per status reachable by some transition (nothing ever moves back to applicant).
    actions = tuple(
        status_action(status)
        for status in VendorStatus
        if any(status in targets for targets in engine.TRANSITIONS.values())
    )

    @admin.display(description="Missing for payout")
    def missing_for_payout(self, obj: Vendor | None) -> str:
        """Required legal fields (`LegalFieldRequirement`) a payout request would still need."""
        if obj is None or obj.pk is None:
            return "-"
        result = legal_profile_completeness(obj)
        if result.complete:
            return "Nothing: the legal profile is complete for payouts."
        parts = []
        if result.missing:
            parts.append("Missing: " + _labels(result.missing))
        if result.invalid:
            parts.append("Invalid: " + _labels(result.invalid))
        return ". ".join(parts) + "."

    @admin.display(description="Onboarding checklist")
    def onboarding(self, obj: Vendor | None) -> SafeString | str:
        """The VEN.5 checklist: only the contract blocks activation; data gaps gate payouts."""
        if obj is None or obj.pk is None:
            return "-"
        checklist = onboarding_checklist(obj)
        version = checklist.current_contract_version
        lines = []
        for item in checklist.items:
            if item.key == engine.CONTRACT:
                text = (
                    "No contract in force"
                    if version is None
                    else f"Contract version {version}: "
                    + ("accepted" if item.done else "not accepted")
                )
            else:
                name = "DAC7 data" if item.key == engine.DAC7 else "Payout data"
                text = f"{name}: " + (
                    "complete" if item.done else "missing " + _field_labels(item.missing)
                )
            if item.blocking and not item.done:
                text += " (Blocks activation)"
            lines.append((text,))
        summary = "Ready to activate" if checklist.ready else "Not ready to activate"
        return format_html(
            "<strong>{}</strong><ul>{}</ul>", summary, format_html_join("", "<li>{}</li>", lines)
        )

    @admin.display(description="Reopen")
    def reopen(self, obj: Vendor | None) -> SafeString | str:
        """A closed vendor is reopened on its own page, with a reason (VEN.21)."""
        if obj is None or obj.pk is None or obj.status != VendorStatus.CLOSED:
            return "-"
        return format_html(
            '<a class="button" href="{}">Reopen this vendor</a>',
            reverse("admin:vendors_vendor_reopen", args=[obj.pk]),
        )

    def get_urls(self) -> list[URLPattern]:
        def reopen(request: HttpRequest, object_id: str) -> HttpResponse:
            # A fresh second factor, like the other sensitive staff pages (CORE.4).
            return step_up(lambda req: self.reopen_view(req, object_id))(request)

        url = path(
            "<path:object_id>/reopen/",
            self.admin_site.admin_view(reopen),
            name=f"{self.opts.app_label}_{self.opts.model_name}_reopen",
        )
        return [url, *super().get_urls()]

    def reopen_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Move a closed vendor back to onboarding (`reopen_vendor`), with a mandatory reason."""
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404("No such vendor.")
        actor = cast(User, request.user)
        if not self.has_change_permission(request, obj) or not can_reopen(actor):
            raise PermissionDenied  # the service checks it too
        change_url = reverse("admin:vendors_vendor_change", args=[obj.pk])
        if obj.status != VendorStatus.CLOSED:
            self.message_user(request, f"{obj} is not closed: nothing to reopen.", messages.WARNING)
            return redirect(change_url)

        form = ReopenForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                reopen_vendor(
                    obj,
                    actor=actor,
                    reason=form.cleaned_data["reason"],
                    ctx=audit_context(request),
                )
            except engine.InvalidTransition as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    f"{obj} was reopened and is onboarding: the owner must accept the contract "
                    "in force again before activation.",
                    messages.SUCCESS,
                )
                return redirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "original": obj,
            "title": f"Reopen {obj}",
            "form": form,
            "change_url": change_url,
        }
        return TemplateResponse(request, REOPEN_TEMPLATE, context)

    @admin.display(description="Audit trail")
    def audit_trail(self, obj: Vendor) -> SafeString | str:
        if obj.pk is None:
            return "-"
        url = f"{reverse('admin:core_auditlog_changelist')}?vendor={obj.public_id}"
        return format_html('<a href="{}">Changes to this vendor</a>', url)

    def get_form(
        self, request: HttpRequest, obj: Vendor | None = None, change: bool = False, **kwargs: Any
    ) -> type[forms.ModelForm[Vendor]]:
        """The add and change forms refuse DAC7 data (a new vendor's, or a change to it) unless
        the staff member verified a second factor recently (VEN.23, CORE.4): the page stays
        open, nothing is saved."""
        form: type[forms.ModelForm[Vendor]] = super().get_form(request, obj, change, **kwargs)
        if is_recently_verified(request):
            return form
        verify_url = reverse("mfa:verify") + "?" + urlencode({"next": request.get_full_path()})

        class StepUpForm(form):  # type: ignore[valid-type,misc]
            def clean(self) -> dict[str, Any]:
                cleaned: dict[str, Any] = super().clean() or {}
                if engine.DAC7_FIELDS & set(self.changed_data):
                    raise ValidationError(
                        format_html(
                            "Correcting the DAC7 dataset needs a fresh second-factor "
                            'verification: <a href="{}">verify</a>, then save again.',
                            verify_url,
                        )
                    )
                return cleaned

        return StepUpForm

    def save_model(self, request: HttpRequest, obj: Vendor, form: Any, change: bool) -> None:
        ctx = audit_context(request)
        if engine.DAC7_FIELDS & set(form.changed_data):
            changes = correct_dac7_dataset(obj, ctx=ctx, stepped_up=is_recently_verified(request))
        else:
            changes = save_vendor(obj, ctx=ctx)
        warn_about_establishment(self, request, obj, changes)

    def delete_model(self, request: HttpRequest, obj: Vendor) -> None:
        delete_vendor(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Vendor]) -> None:
        ctx = audit_context(request)
        for vendor in queryset:
            delete_vendor(vendor, ctx=ctx)


def _locked(obj: VendorContract | None) -> bool:
    return obj is not None and obj.is_accepted


class VendorContractTranslationInline(
    admin.StackedInline[VendorContractTranslation, VendorContract]
):
    model = VendorContractTranslation
    extra = 1
    fields = ("language", "status", "title", "body")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_add_permission(request, obj) and not _locked(obj)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_change_permission(request, obj) and not _locked(obj)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_delete_permission(request, obj) and not _locked(obj)


class VendorContractForm(forms.ModelForm[VendorContract]):
    class Meta:
        model = VendorContract
        fields = ("valid_from", "note")

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        valid_from = cleaned.get("valid_from")
        if valid_from is not None:
            probe = VendorContract(
                pk=self.instance.pk, version=self.instance.version, valid_from=valid_from
            )
            try:
                check_contract_start(probe)
            except ValidationError as exc:
                self.add_error(None, exc)
        return cleaned


@admin.register(VendorContract)
class VendorContractAdmin(admin.ModelAdmin[VendorContract]):
    """Contract versions (VEN.5). A version nobody accepted yet can be edited; an accepted one
    is read-only, and versions are never deleted: new terms are a new version."""

    form = VendorContractForm
    list_display = ("version", "valid_from", "note", "acceptance_count")
    readonly_fields = ("version", "public_id", "created_at")
    fields = ("version", "valid_from", "note", "public_id", "created_at")
    inlines = (VendorContractTranslationInline,)

    @admin.display(description="Acceptances")
    def acceptance_count(self, obj: VendorContract) -> int:
        return obj.acceptances.count()

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_change_permission(request, obj) and not _locked(obj)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(
        self, request: HttpRequest, obj: VendorContract, form: Any, change: bool
    ) -> None:
        save_contract(obj, ctx=audit_context(request))


@admin.register(ContractAcceptance)
class ContractAcceptanceAdmin(admin.ModelAdmin[ContractAcceptance]):
    """Who accepted which contract version, when and from where. Read-only: append-only rows
    written by `services.accept_contract`."""

    list_display = ("vendor", "contract", "user", "language", "accepted_at", "ip")
    list_filter = ("contract", "language")
    search_fields = ("vendor__display_name", "vendor__legal_name", "user__email")
    list_select_related = ("vendor", "contract", "user")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(LegalFieldRequirement)
class LegalFieldRequirementAdmin(admin.ModelAdmin[LegalFieldRequirement]):
    """Which legal fields vendors need, per country and legal form. Every change is audited."""

    list_display = ("field_name", "country", "region", "legal_form", "purpose", "active", "note")
    list_filter = ("purpose", "active", "country", "region", "legal_form", "field_name")
    search_fields = ("field_name", "country", "note")
    fields = ("country", "region", "legal_form", "field_name", "purpose", "active", "note")

    def save_model(
        self, request: HttpRequest, obj: LegalFieldRequirement, form: Any, change: bool
    ) -> None:
        save_requirement(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: LegalFieldRequirement) -> None:
        delete_requirement(obj, ctx=audit_context(request))

    def delete_queryset(
        self, request: HttpRequest, queryset: QuerySet[LegalFieldRequirement]
    ) -> None:
        ctx = audit_context(request)
        for requirement in queryset:
            delete_requirement(requirement, ctx=ctx)


def _overrides_kill_switch(override: VendorFeatureFlag | None) -> bool:
    spec = None if override is None else override.flag.spec
    return spec is not None and spec.kill_switch


@admin.register(VendorFeatureFlag)
class VendorFeatureFlagAdmin(admin.ModelAdmin[VendorFeatureFlag]):
    """Per-vendor feature flag overrides (ARC.8). Pages that can change a kill switch ask for a
    fresh second factor (CORE.4 step-up): the add page (the flag is chosen there) and the change
    and delete pages of a kill switch override. No bulk delete: it would bypass the step-up."""

    list_display = ("vendor", "flag", "kill_switch", "enabled", "note", "updated_at")
    list_filter = ("flag", "enabled")
    search_fields = ("vendor__display_name", "vendor__legal_name", "flag__name", "note")
    list_select_related = ("vendor", "flag")
    autocomplete_fields = ("vendor",)
    fields = ("vendor", "flag", "enabled", "note")
    actions = None

    @admin.display(description="Kill switch", boolean=True)
    def kill_switch(self, obj: VendorFeatureFlag) -> bool:
        return _overrides_kill_switch(obj)

    def get_readonly_fields(
        self, request: HttpRequest, obj: VendorFeatureFlag | None = None
    ) -> tuple[str, ...]:
        # The change page decides on step-up from the stored flag, so the identity of an
        # existing override is fixed: swapping in a kill switch would skip the second factor.
        if obj is not None:
            return ("vendor", "flag")
        return ()

    def save_model(
        self, request: HttpRequest, obj: VendorFeatureFlag, form: Any, change: bool
    ) -> None:
        save_vendor_flag(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: VendorFeatureFlag) -> None:
        delete_vendor_flag(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[VendorFeatureFlag]) -> None:
        ctx = audit_context(request)
        for override in queryset:
            delete_vendor_flag(override, ctx=ctx)

    def add_view(
        self, request: HttpRequest, form_url: str = "", extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().add_view
        return step_up(lambda req: parent(req, form_url, extra_context))(request)

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

        if _overrides_kill_switch(self.get_object(request, object_id)):
            return step_up(view)(request)
        return view(request)

    def delete_view(
        self, request: HttpRequest, object_id: str, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().delete_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, extra_context)

        if _overrides_kill_switch(self.get_object(request, object_id)):
            return step_up(view)(request)
        return view(request)


@admin.register(CrossVendorConsent)
class CrossVendorConsentAdmin(admin.ModelAdmin[CrossVendorConsent]):
    """A vendor's opt-in to other vendors' suggestions and bundles (CAT.14). Versions are
    append-only: staff record a new one, starting now, through the audited service."""

    list_display = ("vendor", "in_suggestions", "in_bundles", "valid_from", "set_by")
    list_filter = ("in_suggestions", "in_bundles")
    search_fields = ("vendor__display_name", "vendor__legal_name")
    list_select_related = ("vendor",)
    raw_id_fields = ("vendor",)
    fields = ("vendor", "in_suggestions", "in_bundles", "valid_from", "set_by")
    readonly_fields = ("valid_from", "set_by")

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(
        self, request: HttpRequest, obj: CrossVendorConsent, form: Any, change: bool
    ) -> None:
        changes = set_cross_vendor_consent(
            obj.vendor,
            in_suggestions=obj.in_suggestions,
            in_bundles=obj.in_bundles,
            ctx=audit_context(request),
        )
        # The admin links to and logs the row the service wrote.
        saved = CrossVendorConsent.objects.get(public_id=changes.changes[0].key)
        obj.pk, obj.valid_from, obj.set_by = saved.pk, saved.valid_from, saved.set_by
        obj._state.adding = False
