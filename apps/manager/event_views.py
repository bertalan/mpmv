"""Manager shop space: the vendor's own events (EVT.4 part 1, spec 15 "Vendor area").

A list with status, dates and the moderation state, one structured form for create and edit,
and the submit and cancel actions. Thin adapter over the EVT.3 services in
`apps.events.services`: they own the state machine, the audit and `dry_run`. Every event is
found through `scoped()`, so another shop's public id is a 404 before a service runs.
Saving, submitting and cancelling are the group 1 session action `content_edit` (CORE.25).
"""

import zoneinfo
from dataclasses import replace
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import formats
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from apps.accounts.decorators import requires_level_method
from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.events import engine, services
from apps.events.models import EventPage
from apps.events.panels import map_assets, map_settings
from apps.vendors.engine import VendorCannotWrite

from .event_block_panel import blocks_context
from .event_forms import EventForm, initial_from_page
from .views import ManagerView

SECTION = "shop.events"
LIST_URL = "manager:shop:events"
EDIT_URL = "manager:shop:event_edit"


def _not_active() -> str:
    return _("Your shop is not active yet, so events cannot be changed.")


def _when(page: EventPage) -> str:
    if page.all_day:
        if page.start_date is None or page.end_date is None:
            return ""
        first = formats.date_format(page.start_date, "SHORT_DATE_FORMAT")
        last = formats.date_format(page.end_date, "SHORT_DATE_FORMAT")
        return first if first == last else f"{first} - {last}"
    if page.starts_at is None:
        return ""
    try:
        zone = zoneinfo.ZoneInfo(page.timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return formats.date_format(page.starts_at, "SHORT_DATETIME_FORMAT")
    return formats.date_format(page.starts_at.astimezone(zone), "SHORT_DATETIME_FORMAT")


def _can_submit(page: EventPage) -> bool:
    if page.hidden_at is not None or page.status in (EventPage.Status.PENDING, "cancelled"):
        return False
    return page.status == EventPage.Status.DRAFT or bool(page.has_unpublished_changes)


def _can_cancel(page: EventPage) -> bool:
    return (
        page.hidden_at is None
        and page.live
        and page.cancelled_at is None
        and page.status == EventPage.Status.PUBLISHED
    )


def _moderation(page: EventPage) -> str:
    if page.hidden_at is not None:
        reason = page.hidden_reason
        return (
            _("Hidden by the platform: %(reason)s") % {"reason": reason}
            if reason
            else _("Hidden by the platform.")
        )
    comment = services.requested_changes_comment(page)
    if comment:
        return _("Changes requested: %(comment)s") % {"comment": comment}
    return ""


class EventsView(ManagerView):
    """The selected shop's events, newest first."""

    section = SECTION
    template_name = "manager/shop/events.html"
    paginate_by = 25

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rows = self.scoped(EventPage).select_related("locale").order_by("-id")
        page = Paginator(rows, self.paginate_by).get_page(self.request.GET.get("page"))
        context.update(
            page_obj=page,
            events=[
                {
                    "title": event.title,
                    "status": event.get_status_display(),
                    "when": _when(event),
                    "language": event.locale.language_code,
                    "moderation": _moderation(event),
                    "edit_url": reverse(EDIT_URL, args=[event.public_id]),
                    "submit_url": reverse("manager:shop:event_submit", args=[event.public_id])
                    if _can_submit(event)
                    else "",
                    "cancel_url": reverse("manager:shop:event_cancel", args=[event.public_id])
                    if _can_cancel(event)
                    else "",
                }
                for event in page.object_list
            ],
            new_url=reverse("manager:shop:event_new"),
            previous_url=f"?page={page.previous_page_number()}" if page.has_previous() else "",
            next_url=f"?page={page.next_page_number()}" if page.has_next() else "",
        )
        return context


class _EventAction(ManagerView):
    """A page of the shop's events that may act on one of them (found through `scoped()`)."""

    section = SECTION

    def event(self) -> EventPage:
        return get_object_or_404(self.scoped(EventPage), public_id=self.kwargs["public_id"])

    def actor(self) -> User:
        actor = self.manager.user
        if not isinstance(actor, User):
            raise Http404("No such event.")
        return actor

    def audit(self, request: HttpRequest) -> Any:
        return replace(audit_context(request), source=AuditLog.Source.VENDOR)


class EventFormView(_EventAction):
    """Create (no id) or edit (an id) one event with the structured form."""

    template_name = "manager/shop/event_form.html"

    def page(self) -> EventPage | None:
        return self.event() if "public_id" in self.kwargs else None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        page = self.page()
        if "form" not in context:
            vendor = self.manager.vendor
            if page is None:
                context["form"] = EventForm(vendor=vendor)
            else:
                latest: EventPage = page.get_latest_revision_as_object()
                context["form"] = EventForm(
                    initial=initial_from_page(latest),
                    kept_rule=_kept(latest),
                    vendor=vendor,
                    page=latest,
                )
        context["event"] = page
        context["title"] = _("Edit the event") if page else _("New event")
        if page is not None:
            context["can_submit"] = _can_submit(page)
            context["submit_url"] = reverse("manager:shop:event_submit", args=[page.public_id])
            context["cancel_url"] = reverse("manager:shop:event_cancel", args=[page.public_id])
            context["can_cancel"] = _can_cancel(page)
            context["moderation"] = _moderation(page)
            context["preview_url"] = reverse("manager:shop:event_preview", args=[page.public_id])
            context.update(blocks_context(page))
        form = context["form"]
        context["map_media"] = map_assets()
        context["map_picker"] = {
            **form.picker_context(),
            **map_settings(),
            "find_url": reverse("geocoding_find"),
            "language": get_language() or "",
            "help_id": "event-map-help",
        }
        context["images_url"] = reverse("manager:shop:event_images")
        context["list_url"] = reverse(LIST_URL)
        return context

    @requires_level_method("content_edit")  # group 1: any session, CORE.26 can tighten it
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page, vendor, actor = self.page(), self.manager.vendor, self.actor()
        if vendor is None:
            raise Http404("No vendor selected.")
        latest = page.get_latest_revision_as_object() if page is not None else None
        kept = _kept(latest) if latest is not None else ""
        form = EventForm(request.POST, kept_rule=kept, vendor=vendor, page=latest)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            changes = services.save_event_draft(
                actor, form.event_data(), page, vendor=vendor, ctx=self.audit(request)
            )
        except ValidationError as error:
            _add_errors(form, error)
            return self.render_to_response(self.get_context_data(form=form))
        except services.EventRefused:
            return self.render_to_response(self.get_context_data(form=form), status=403)
        except VendorCannotWrite:
            form.add_error(None, _not_active())
            return self.render_to_response(self.get_context_data(form=form))
        saved = changes.page if changes.page is not None else page
        assert saved is not None  # noqa: S101  # a real save always has its page
        messages.success(request, _("The event is saved as a draft. Submit it when it is ready."))
        return HttpResponseRedirect(reverse(EDIT_URL, args=[saved.public_id]))


def _kept(page: EventPage) -> str:
    """The saved rule that the presets cannot write ("" when it is a preset or empty)."""
    rule = page.rrule
    zone = page.timezone or "UTC"
    try:
        known = engine.preset_from_rrule(rule, all_day=page.all_day, timezone=zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        known = None
    return rule if rule and known is None else ""


def _add_errors(form: EventForm, error: ValidationError) -> None:
    if hasattr(error, "error_dict"):
        for name, errors in error.message_dict.items():
            target = {"rrule": "recurrence"}.get(name, name)
            for message in errors:
                form.add_error(target if target in form.fields else None, message)
    else:
        for message in error.messages:
            form.add_error(None, message)


class EventSubmitView(_EventAction):
    """POST: submit the latest revision for review (or publish it, when the policy allows)."""

    http_method_names = ["post", "options"]

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()  # another shop's id is a 404 here, before anything runs
        try:
            services.submit_event(self.actor(), page, ctx=self.audit(request))
        except VendorCannotWrite:
            messages.error(request, _not_active())
        except (ValidationError, services.EventRefused) as error:
            messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        else:
            messages.success(request, _("The event is submitted."))
        return HttpResponseRedirect(reverse(LIST_URL))


class EventCancelView(_EventAction):
    """GET asks for the confirmation, POST cancels a published event in every language."""

    template_name = "manager/shop/event_cancel.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["event"] = self.event()
        context["title"] = _("Cancel the event")
        context["list_url"] = reverse(LIST_URL)
        return context

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()
        try:
            services.cancel_event(self.actor(), page, ctx=self.audit(request))
        except VendorCannotWrite:
            messages.error(request, _not_active())
        except (ValidationError, services.EventRefused) as error:
            messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        else:
            messages.success(request, _("The event is cancelled. Its page shows a cancellation."))
        return HttpResponseRedirect(reverse(LIST_URL))
