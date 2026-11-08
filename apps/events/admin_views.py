"""The moderation UI of events in the Wagtail admin (EVT.3 part 2/2, spec 15 "Staff admin").

Thin views over `apps.events.services`: staff hide (with a reason), restore and cancel an
event, and change a vendor's moderation policy and the events settings; staff and the "Event
moderators" group see the events awaiting "Event review". Approving and requesting changes stay
in Wagtail's own workflow UI, which the review list links to. Every action is one service call,
and so one audit entry. The templates carry no inline script or style (CSP).
"""

import uuid
from collections.abc import Callable
from typing import Any

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from wagtail.models import Site

from apps.core.audit import audit_context
from apps.vendors.models import Vendor

from . import services
from .models import EventPage, EventSettings, PolicyMode, VendorEventPolicy

POSITIVE_SMALL_INT_MAX = 32767


class HideForm(forms.Form):
    reason = forms.CharField(
        label=gettext_lazy("Reason"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=gettext_lazy("Shown to the vendor and kept in the audit log."),
    )
    dsa_notice_ref = forms.CharField(
        label=gettext_lazy("DSA notice reference"),
        required=False,
        max_length=64,
        help_text=gettext_lazy("Only when the event is hidden after a notice."),
    )


class ConfirmForm(forms.Form):
    """No fields: the POST itself confirms; service errors show as non-field errors."""


class PolicyForm(forms.Form):
    vendor = forms.ModelChoiceField(
        label=gettext_lazy("Vendor"),
        queryset=Vendor.objects.order_by("display_name", "pk"),
        to_field_name="public_id",
    )
    mode = forms.ChoiceField(label=gettext_lazy("Moderation"), choices=PolicyMode.choices)


class SettingsForm(forms.Form):
    site = forms.ModelChoiceField(label=gettext_lazy("Site"), queryset=Site.objects.all())
    moderation_threshold = forms.IntegerField(
        label=gettext_lazy("Moderation threshold"),
        min_value=0,
        max_value=POSITIVE_SMALL_INT_MAX,
        help_text=gettext_lazy("A vendor's first events up to this number go through review."),
    )
    cancelled_retention_days = forms.IntegerField(
        label=gettext_lazy("Days a cancelled event stays in feeds"),
        min_value=0,
        max_value=POSITIVE_SMALL_INT_MAX,
    )


def _require(check: Callable[[Any], bool], request: HttpRequest) -> None:
    if not check(request.user):
        raise PermissionDenied


def _event(page_id: int) -> EventPage:
    event: EventPage = get_object_or_404(EventPage, pk=page_id)
    return event


def _edit_url(event: EventPage) -> str:
    return reverse("wagtailadmin_pages:edit", args=(event.pk,))


def _render(request: HttpRequest, template: str, context: dict[str, Any]) -> TemplateResponse:
    return TemplateResponse(request, f"events/admin/{template}", context)


def hide(request: HttpRequest, page_id: int) -> HttpResponse:
    """Hide an event: unpublish it in every language, with the reason (`hide_event`)."""
    _require(services.may_moderate_events, request)
    event = _event(page_id)
    form = HideForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            services.hide_event(
                request.user,
                event,
                form.cleaned_data["reason"],
                form.cleaned_data["dsa_notice_ref"],
                ctx=audit_context(request),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Event '%(title)s' hidden.") % {"title": event.title})
            return redirect(_edit_url(event))
    return _render(
        request,
        "hide.html",
        {
            "page_title": _("Hide event"),
            "page_subtitle": event.title,
            "header_icon": "view",
            "event": event,
            "form": form,
            "edit_url": _edit_url(event),
        },
    )


def _confirm(
    request: HttpRequest,
    page_id: int,
    action: Callable[..., Any],
    *,
    title: str,
    done: str,
    explanation: str,
) -> HttpResponse:
    _require(services.may_moderate_events, request)
    event = _event(page_id)
    form = ConfirmForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            action(request.user, event, ctx=audit_context(request))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, done % {"title": event.title})
            return redirect(_edit_url(event))
    return _render(
        request,
        "confirm.html",
        {
            "page_title": title,
            "page_subtitle": event.title,
            "header_icon": "doc-full",
            "event": event,
            "form": form,
            "explanation": explanation,
            "submit_label": title,
            "edit_url": _edit_url(event),
        },
    )


def restore(request: HttpRequest, page_id: int) -> HttpResponse:
    """Restore a hidden event: republish its latest approved revision (`restore_event`)."""
    return _confirm(
        request,
        page_id,
        services.restore_event,
        title=_("Restore event"),
        done=_("Event '%(title)s' restored."),
        explanation=_(
            "The event comes back online in every language with its latest approved version."
        ),
    )


def cancel(request: HttpRequest, page_id: int) -> HttpResponse:
    """Cancel a live event: it stays online marked as cancelled (`cancel_event`)."""
    return _confirm(
        request,
        page_id,
        services.cancel_event,
        title=_("Cancel event"),
        done=_("Event '%(title)s' cancelled."),
        explanation=_(
            "The event stays online in every language, marked as cancelled, and calendars "
            "that subscribed to it are updated."
        ),
    )


def review(request: HttpRequest) -> HttpResponse:
    """The events awaiting "Event review", linking to Wagtail's review UI (the edit view)."""
    _require(services.may_review_events, request)
    return _render(
        request,
        "review.html",
        {
            "page_title": _("Events awaiting review"),
            "header_icon": "tasks",
            "items": services.events_awaiting_review(),
        },
    )


def policies(request: HttpRequest) -> HttpResponse:
    """Every vendor's moderation override."""
    _require(services.may_moderate_events, request)
    return _render(
        request,
        "policies.html",
        {
            "page_title": _("Vendor moderation policies"),
            "header_icon": "group",
            "policies": services.vendor_event_policies(),
        },
    )


def policy_set(request: HttpRequest) -> HttpResponse:
    """Set a vendor's moderation override (`set_vendor_policy`)."""
    _require(services.may_moderate_events, request)
    if request.method == "POST":
        form = PolicyForm(request.POST)
        if form.is_valid():
            try:
                services.set_vendor_policy(
                    request.user,
                    form.cleaned_data["vendor"],
                    form.cleaned_data["mode"],
                    ctx=audit_context(request),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _("Moderation policy saved."))
                return redirect("events_admin:policies")
    else:
        initial: dict[str, Any] = {"mode": PolicyMode.DEFAULT}
        public_id = _uuid(request.GET.get("vendor"))
        vendor = Vendor.objects.filter(public_id=public_id).first() if public_id else None
        if vendor is not None:
            initial["vendor"] = vendor.public_id
            policy = VendorEventPolicy.objects.filter(vendor=vendor).first()
            if policy is not None:
                initial["mode"] = policy.mode
        form = PolicyForm(initial=initial)
    return _render(
        request,
        "form.html",
        {
            "page_title": _("Set a vendor's moderation policy"),
            "header_icon": "group",
            "form": form,
            "submit_label": _("Save"),
            "back_url": reverse("events_admin:policies"),
        },
    )


def _uuid(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(value or ""))
    except ValueError:
        return None


def event_settings(request: HttpRequest) -> HttpResponse:
    """Change a site's events settings (`set_event_settings`)."""
    _require(services.may_moderate_events, request)
    if request.method == "POST":
        form = SettingsForm(request.POST)
        if form.is_valid():
            try:
                services.set_event_settings(
                    request.user,
                    form.cleaned_data["site"],
                    moderation_threshold=form.cleaned_data["moderation_threshold"],
                    cancelled_retention_days=form.cleaned_data["cancelled_retention_days"],
                    ctx=audit_context(request),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _("Events settings saved."))
                return redirect(
                    reverse("events_admin:settings") + f"?site={form.cleaned_data['site'].pk}"
                )
    else:
        site = (
            Site.objects.filter(pk=_int(request.GET.get("site"))).first()
            or Site.objects.filter(is_default_site=True).first()
        )
        initial: dict[str, Any] = {}
        if site is not None:
            current = EventSettings.for_site(site)
            initial = {
                "site": site.pk,
                "moderation_threshold": current.moderation_threshold,
                "cancelled_retention_days": current.cancelled_retention_days,
            }
        form = SettingsForm(initial=initial)
    return _render(
        request,
        "form.html",
        {
            "page_title": _("Events settings"),
            "header_icon": "cog",
            "form": form,
            "submit_label": _("Save"),
            "back_url": "",
        },
    )


def _int(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None
