"""Staff admin for vendors, their translations and the legal field requirements
(Wagtail admin replaces this in CORE.8).

Vendor writes go through the audited services (SEC.4); the change page links to the vendor's
audit trail.
"""

import functools
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

from apps.accounts.decorators import level_gated_action, requires_level, step_up_required
from apps.accounts.mfa_session import is_recently_verified
from apps.accounts.models import User
from apps.core.admin import step_up
from apps.core.audit import audit_context
from apps.core.changeset import ChangeSet
from apps.core.text import ascii_int

from . import engine
from .models import (
    ContractAcceptance,
    CrossVendorConsent,
    LegalFieldRequirement,
    NoTinCountry,
    Vendor,
    VendorClaim,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorStatus,
    VendorTranslation,
    legal_field_label,
)
from .services import (
    add_no_tin_country,
    approve_claim,
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
    no_tin_impact,
    onboarding_checklist,
    reject_claim,
    remove_no_tin_country,
    reopen_vendor,
    save_contract,
    save_requirement,
    save_vendor,
    save_vendor_flag,
    send_claim_link,
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


BANK_FIELDS = ("iban", "bic", "bank_name", "account_holder_name")  # D-32 `payout_details`
ClaimAction = Callable[
    [admin.ModelAdmin[Vendor], HttpRequest, QuerySet[Vendor]], HttpResponse | None
]
_STEP_UP_PASSED = HttpResponse()


def _step_up_passed(request: HttpRequest) -> HttpResponse:
    return _STEP_UP_PASSED


def step_up_action(action: ClaimAction) -> ClaimAction:
    """Run `action` only behind a fresh second factor (CORE.4), checked on the request by the
    action itself: whatever `action` values the changelist POST carries, a stale factor gets
    the verify (or setup) redirect and nothing changes."""

    @functools.wraps(action)
    def guarded(
        modeladmin: admin.ModelAdmin[Vendor], request: HttpRequest, queryset: QuerySet[Vendor]
    ) -> HttpResponse | None:
        refused = step_up_required()(_step_up_passed)(request)
        if refused is not _STEP_UP_PASSED:
            return refused
        return action(modeladmin, request, queryset)

    return guarded


def _claim_admin_action(
    name: str, description: str, action: ClaimAction, *, level: str | None = None
) -> ClaimAction:
    action.__name__ = name
    # TODO(CORE.18): granting the owner role (approve) needs its own permission from the
    # staff role matrix, not just `vendors.change_vendor`.
    guarded = step_up_action(action)
    if level is not None:  # D-32: a per-action passkey on top of the second factor
        guarded = level_gated_action(level)(guarded)
    return admin.action(description=description, permissions=["change"])(guarded)


def send_claim_link_action(
    modeladmin: admin.ModelAdmin[Vendor], request: HttpRequest, queryset: QuerySet[Vendor]
) -> None:
    """Send the claim link (VEN.24) of each selected vendor to its legal email of record."""
    ctx = audit_context(request)
    for vendor in queryset:
        resent = VendorClaim.objects.filter(vendor=vendor).exists()
        try:
            send_claim_link(vendor, ctx=ctx)
        except engine.ClaimError as exc:
            modeladmin.message_user(request, f"{vendor}: {exc}", messages.ERROR)
            continue
        done = "the claim link was sent to the legal email of record."
        if resent:
            done = "a new claim link was sent; the previous link no longer works."
        modeladmin.message_user(request, f"{vendor}: {done}", messages.SUCCESS)


CLAIM_DECISION_TEMPLATE = "admin/vendors/vendor/claim_decision.html"
DECISIONS = {
    "approve_claim": (approve_claim, "Approve", "claim approved: the claimant is now the owner."),
    "reject_claim": (reject_claim, "Reject", "claim rejected: nothing was granted."),
}


def _reviewed_field(vendor: Vendor) -> str:
    return f"reviewed-{vendor.pk}"


def _reviewed_value(claim: VendorClaim) -> str:
    return f"{claim.version}:{claim.claimant_id}"


def _parse_reviewed(value: str) -> tuple[int, int] | None:
    first, _, second = value.partition(":")
    version, claimant = ascii_int(first), ascii_int(second)
    if version is None or claimant is None:
        return None
    return version, claimant


def _review_page(
    modeladmin: admin.ModelAdmin[Vendor],
    request: HttpRequest,
    queryset: QuerySet[Vendor],
    name: str,
) -> HttpResponse:
    """The claims staff are about to decide, each with its claimant and link version; the
    confirmation posts back what was shown, and the decision is bound to it."""
    rows: list[dict[str, Any]] = []
    reviewed: dict[str, str] = {}
    for vendor in queryset:
        claim = VendorClaim.objects.filter(vendor=vendor).select_related("claimant").first()
        if claim is None or claim.status != VendorClaim.Status.SUBMITTED or claim.claimant is None:
            rows.append({"vendor": vendor, "error": "no claim is waiting for approval"})
            continue
        reviewed[_reviewed_field(vendor)] = _reviewed_value(claim)
        rows.append({"vendor": vendor, "claim": claim})
    context = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.opts,
        "title": f"{DECISIONS[name][1]} claims",
        "verb": DECISIONS[name][1],
        "action": name,
        "rows": rows,
        "selected": [vendor.pk for vendor in queryset],
        "reviewed_fields": reviewed,
    }
    return TemplateResponse(request, CLAIM_DECISION_TEMPLATE, context)


def decision_action(name: str) -> ClaimAction:
    """Approve or reject the submitted claims (VEN.24) of the selected vendors: a review page
    first, then the decision on exactly the claims that page showed."""

    def action(
        modeladmin: admin.ModelAdmin[Vendor], request: HttpRequest, queryset: QuerySet[Vendor]
    ) -> HttpResponse | None:
        if request.POST.get("post") != "yes":
            return _review_page(modeladmin, request, queryset, name)
        decide, _verb, done = DECISIONS[name]
        ctx = audit_context(request)
        for vendor in queryset:
            reviewed = _parse_reviewed(request.POST.get(_reviewed_field(vendor), ""))
            claim = VendorClaim.objects.filter(vendor=vendor).first()
            if reviewed is None or claim is None:
                modeladmin.message_user(
                    request, f"{vendor}: review the claim before deciding it.", messages.ERROR
                )
                continue
            version, claimant_id = reviewed
            try:
                decide(claim, ctx=ctx, reviewed_version=version, reviewed_claimant_id=claimant_id)
            except engine.ClaimError as exc:
                modeladmin.message_user(request, f"{vendor}: {exc}", messages.ERROR)
            else:
                modeladmin.message_user(request, f"{vendor}: {done}", messages.SUCCESS)
        return None

    return action


CLAIM_ACTIONS = (
    _claim_admin_action(
        "send_claim_link",
        "Send or regenerate the claim link (imported vendors)",
        send_claim_link_action,
    ),
    _claim_admin_action(
        "approve_claim",
        "Approve the submitted claim",
        decision_action("approve_claim"),
        level="owner_role_grant",  # D-32 group 3: it grants the owner role
    ),
    _claim_admin_action(
        "reject_claim", "Reject the submitted claim", decision_action("reject_claim")
    ),
)
# Sending a link restarts a claim (after a reject only staff may), deciding one grants or
# refuses the owner role: all behind a fresh second factor, checked by each action itself and,
# before Django picks the action, for every `action` value the POST carries.
STEP_UP_CLAIM_ACTIONS = frozenset({"send_claim_link", "approve_claim", "reject_claim"})
CLAIM_LINK_TEMPLATE = "admin/vendors/vendor/claim_link.html"


class ClaimLinkForm(forms.Form):
    """Where the claim link goes: the legal email of record, or the PEC address on file. No
    other address can be typed or chosen (decision on PR #141)."""

    channel = forms.ChoiceField(label="Send the link to", widget=forms.RadioSelect)

    def __init__(self, vendor: Vendor, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        labels = {
            engine.CLAIM_CHANNEL_EMAIL: f"Legal email of record ({vendor.contact_email})",
            engine.CLAIM_CHANNEL_PEC: f"PEC address ({vendor.pec})",
        }
        channels = engine.claim_channels(contact_email=vendor.contact_email, pec=vendor.pec)
        field = cast(forms.ChoiceField, self.fields["channel"])
        field.choices = [(channel, labels[channel]) for channel in channels]
        field.initial = engine.CLAIM_CHANNEL_EMAIL


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
        "claim_status",
        "missing_for_payout",
        "vies_status",
        "vies_checked_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("display_name", "slug", "status", "public_id", "audit_trail")}),
        ("Onboarding", {"fields": ("onboarding", "onboarding_round", "reopen", "claim_status")}),
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
            {"fields": BANK_FIELDS},
        ),
        ("Contact", {"fields": ("contact_email", "phone")}),
        ("Sales", {"fields": ("sales_mode_default", "enquiries_enabled")}),
        ("Migration", {"fields": ("legacy_wp_user_id",)}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (VendorTranslationInline,)
    # One action per status reachable by some transition (nothing ever moves back to applicant).
    actions = (
        *(
            status_action(status)
            for status in VendorStatus
            if any(status in targets for targets in engine.TRANSITIONS.values())
        ),
        *CLAIM_ACTIONS,
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
        """The VEN.5 checklist: the contract and the platform duties (CMP.25) block activation; DAC7
        and payout data gaps gate payouts."""
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
                name = {
                    engine.DAC7: "DAC7 data",
                    engine.PLATFORM_DUTIES: "Platform duties",
                }.get(item.key, "Payout data")
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

    @admin.display(description="Claim")
    def claim_status(self, obj: Vendor | None) -> SafeString | str:
        """VEN.24: the claim of an imported vendor; the list actions send, approve or reject."""
        if obj is None or obj.pk is None:
            return "-"
        claim = VendorClaim.objects.filter(vendor=obj).select_related("claimant").first()
        if claim is None:
            text = "No claim link sent."
        else:
            text = f"{claim.get_status_display()} (link version {claim.version})"
            text += f", sent to the {claim.get_channel_display().lower()}"
            if claim.claimant is not None:
                text += f", claimant {claim.claimant.email}"
        return format_html(
            '{} <a class="button" href="{}">Send or regenerate the claim link</a>',
            text,
            reverse("admin:vendors_vendor_claim_link", args=[obj.pk]),
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

    def changeform_view(
        self,
        request: HttpRequest,
        object_id: str | None = None,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Changing the invoicing bank details needs a per-action passkey (D-32 group 3,
        `payout_details`); other edits do not."""
        parent = super().changeform_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, form_url, extra_context)

        if request.method == "POST" and self._posts_bank_change(request, object_id):
            return requires_level("payout_details")(step_up(view))(request)
        return view(request)

    def _posts_bank_change(self, request: HttpRequest, object_id: str | None) -> bool:
        stored = self.get_object(request, unquote(object_id)) if object_id else None
        for name in BANK_FIELDS:
            if name not in request.POST:
                continue
            before = "" if stored is None else str(getattr(stored, name, "") or "")
            if request.POST[name] != before:
                return True
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Deciding a claim (it grants the owner role, or refuses it) needs a fresh second
        factor, like the other sensitive staff pages (CORE.4); browsing the list does not."""
        parent = super().changelist_view
        # Django runs `getlist("action")[index]`: guard on every value, not just one of them.
        if request.method == "POST" and STEP_UP_CLAIM_ACTIONS.intersection(
            request.POST.getlist("action")
        ):
            return step_up(lambda req: parent(req, extra_context))(request)
        return parent(request, extra_context)

    def get_urls(self) -> list[URLPattern]:
        def reopen(request: HttpRequest, object_id: str) -> HttpResponse:
            # A fresh second factor, like the other sensitive staff pages (CORE.4).
            return step_up(lambda req: self.reopen_view(req, object_id))(request)

        def claim_link(request: HttpRequest, object_id: str) -> HttpResponse:
            # Sending restarts a claim, also after a reject: a fresh second factor (CORE.4).
            return step_up(lambda req: self.claim_link_view(req, object_id))(request)

        prefix = f"{self.opts.app_label}_{self.opts.model_name}"
        urls = [
            path(
                "<path:object_id>/reopen/",
                self.admin_site.admin_view(reopen),
                name=f"{prefix}_reopen",
            ),
            path(
                "<path:object_id>/claim-link/",
                self.admin_site.admin_view(claim_link),
                name=f"{prefix}_claim_link",
            ),
        ]
        return [*urls, *super().get_urls()]

    def claim_link_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Send or regenerate the claim link (VEN.24), to the legal email of record or to the
        PEC address on file; `send_claim_link` audits the channel and the masked address."""
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404("No such vendor.")
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        change_url = reverse("admin:vendors_vendor_change", args=[obj.pk])
        form = ClaimLinkForm(obj, request.POST or None)
        if request.method == "POST" and form.is_valid():
            resent = VendorClaim.objects.filter(vendor=obj).exists()
            channel = form.cleaned_data["channel"]
            try:
                send_claim_link(obj, channel=channel, ctx=audit_context(request))
            except engine.ClaimError as exc:
                form.add_error(None, str(exc))
            else:
                done = "a new claim link was sent; the previous link no longer works."
                if not resent:
                    done = "the claim link was sent."
                self.message_user(request, f"{obj}: {done}", messages.SUCCESS)
                return redirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "original": obj,
            "title": f"Send the claim link of {obj}",
            "form": form,
            "change_url": change_url,
        }
        return TemplateResponse(request, CLAIM_LINK_TEMPLATE, context)

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


@admin.register(NoTinCountry)
class NoTinCountryAdmin(admin.ModelAdmin[NoTinCountry]):
    """Jurisdictions that issue no TIN (VEN.27), with their dates. Staff add a country with a
    reason (it counts from today) and end it with a reason (it stops counting today); rows are
    never deleted, so past periods stay reproducible. Both go through the audited services. The
    end page asks for the reason, refuses an empty one and shows how many vendors it blocks. No
    bulk action: it has no place to ask for a reason.

    A change relaxes a compliance and payout gate, so every write view needs an operational
    session (CORE.25, D-32 group 2, action `no_tin_country`)."""

    delete_confirmation_template = "admin/vendors/notincountry/delete_confirmation.html"

    list_display = ("country", "reason", "valid_from", "valid_to", "removal_reason")
    list_filter = ("country",)
    search_fields = ("country", "reason")
    fields = ("country", "reason")
    actions = None

    def get_readonly_fields(
        self, request: HttpRequest, obj: NoTinCountry | None = None
    ) -> tuple[str, ...]:
        return ("country", "reason") if obj is not None else ()

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False  # to change a reason, end the listing and add the country again

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        if obj is not None and obj.valid_to is not None:
            return False  # an ended row is history
        return super().has_delete_permission(request, obj)

    def save_model(self, request: HttpRequest, obj: NoTinCountry, form: Any, change: bool) -> None:
        add_no_tin_country(obj.country, reason=obj.reason, ctx=audit_context(request))

    def add_view(
        self, request: HttpRequest, form_url: str = "", extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().add_view
        return requires_level("no_tin_country")(lambda req: parent(req, form_url, extra_context))(
            request
        )

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        parent = super().change_view
        return requires_level("no_tin_country")(
            lambda req: parent(req, object_id, form_url, extra_context)
        )(request)

    def delete_view(
        self, request: HttpRequest, object_id: str, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        if not self.has_delete_permission(request):
            raise PermissionDenied
        parent = super().delete_view

        def view(req: HttpRequest) -> HttpResponse:
            if req.method == "POST" and not req.POST.get("reason", "").strip():
                messages.error(req, "A reason is required to end the listing of a country.")
                return redirect(req.get_full_path())
            row = self.get_object(req, unquote(object_id))
            extra = dict(extra_context or {})
            if row is not None:
                extra["vendors_without_tin"] = no_tin_impact(str(row.country))
            return parent(req, object_id, extra)

        return requires_level("no_tin_country")(view)(request)

    def delete_model(self, request: HttpRequest, obj: NoTinCountry) -> None:
        remove_no_tin_country(
            obj.country, reason=request.POST.get("reason", ""), ctx=audit_context(request)
        )

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[NoTinCountry]) -> None:
        for row in queryset:  # unreachable (no bulk actions); kept on the audited path
            self.delete_model(request, row)


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
