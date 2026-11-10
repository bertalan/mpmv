"""Wagtail admin hooks of the events app (EVT.1 part 2/2): thin, the rules are in `services`.

- Vendors author events in the vendor area, never in the Wagtail admin (spec 15). The
  `apps.vendors.backends` backend refuses them the whole admin; these hooks refuse them every
  event page action as a second line (create, edit, delete, unpublish, move, copy, bulk).
- Only platform staff create events in the admin, by adding or by copying a page (spec 15
  "Permissions": only platform staff create platform events).
- The moderation UI (EVT.3 part 2/2): staff hide, restore and cancel an event from the page
  action menu and the explorer listing; the "Events" menu holds the events awaiting review,
  the events settings (`admin_views`; the vendors' policies page has no menu item, CORE.8).
  A non-staff user
  never edits a hidden event, nor publishes one (alone or in bulk) while its vendor needs
  review: the submission goes through "Event review". wagtail-localize's own views keep the
  same rules through `apps.events.middleware`, and `EventPage.publish` refuses a hidden page on
  every path but `restore_event`.
"""

from collections.abc import Iterator
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.urls import URLPattern, URLResolver, include, path, reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.action_menu import ActionMenuItem
from wagtail.admin.menu import Menu, MenuItem, SubmenuMenuItem
from wagtail.admin.widgets import Button

from . import admin_views
from .models import EventPage
from .services import (
    admin_edit_refused,
    admin_publish_refused,
    may_create_events_in_admin,
    may_edit_events_in_admin,
    may_moderate_events,
    may_review_events,
)

PUBLISH = "action-publish"


def _is_event(page_or_class: Any) -> bool:
    page_class = page_or_class if isinstance(page_or_class, type) else None
    if page_class is None:
        page_class = getattr(page_or_class, "specific_class", None) or type(page_or_class)
    return issubclass(page_class, EventPage)


def _refuse_vendors(request: HttpRequest, page_or_class: Any) -> None:
    if _is_event(page_or_class) and not may_edit_events_in_admin(request.user):
        raise PermissionDenied


def _refuse_non_staff_creation(request: HttpRequest, page_or_class: Any) -> None:
    if _is_event(page_or_class) and not may_create_events_in_admin(request.user):
        raise PermissionDenied


@hooks.register("before_create_page")  # type: ignore[untyped-decorator]
def refuse_creating_events(request: HttpRequest, parent_page: Any, page_class: type[Any]) -> None:
    _refuse_vendors(request, page_class)
    _refuse_non_staff_creation(request, page_class)


@hooks.register("before_copy_page")  # type: ignore[untyped-decorator]
def refuse_copying_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page)
    _refuse_non_staff_creation(request, page)


def _event(page: Any) -> EventPage | None:
    if not _is_event(page):
        return None
    event = page if isinstance(page, EventPage) else page.specific
    return event if isinstance(event, EventPage) else None


@hooks.register("before_edit_page")  # type: ignore[untyped-decorator]
def refuse_vendors_editing_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page)
    event = _event(page)
    if event is None:
        return
    if admin_edit_refused(request.user, event):
        raise PermissionDenied
    # Wagtail's editor posts `action-publish`; wagtail-localize's translation editor, which
    # replaces it on a synced translation, posts `action=publish`.
    publishes = bool(request.POST.get(PUBLISH)) or request.POST.get("action") == "publish"
    if request.method == "POST" and publishes and admin_publish_refused(request.user, event):
        raise PermissionDenied


@hooks.register("before_delete_page")  # type: ignore[untyped-decorator]
def refuse_vendors_deleting_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page)


@hooks.register("before_unpublish_page")  # type: ignore[untyped-decorator]
def refuse_vendors_unpublishing_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page)


@hooks.register("before_move_page")  # type: ignore[untyped-decorator]
def refuse_vendors_moving_events(request: HttpRequest, page: Any, *args: Any) -> None:
    _refuse_vendors(request, page)


@hooks.register("before_bulk_action")  # type: ignore[untyped-decorator]
def refuse_vendors_bulk_acting_on_events(
    request: HttpRequest, action_type: str, objects: Any, action_class_instance: Any
) -> None:
    for obj in objects:
        _refuse_vendors(request, obj)
        event = _event(obj)
        if event is None or action_type != "publish":
            continue
        if admin_publish_refused(request.user, event):
            raise PermissionDenied


@hooks.register("construct_page_action_menu")  # type: ignore[untyped-decorator]
def offer_no_publish_without_review(
    menu_items: list[Any], request: HttpRequest, context: dict[str, Any]
) -> None:
    """A non-staff editor submits to "Event review" instead of publishing."""
    event = _event(context.get("page")) if context.get("page") is not None else None
    if event is not None and admin_publish_refused(request.user, event):
        menu_items[:] = [item for item in menu_items if item.name != PUBLISH]


# --- The staff actions: hide, restore, cancel (EVT.3 part 2/2)


def _staff_actions(event: EventPage) -> list[tuple[str, str, str]]:
    """(URL name, label, icon) of the staff actions the event's state allows."""
    if event.hidden_at is not None:
        return [("restore", str(_("Restore event")), "view")]
    actions = [("hide", str(_("Hide event")), "no-view")]
    if event.live and event.cancelled_at is None:
        actions.append(("cancel", str(_("Cancel event")), "cross"))
    return actions


class EventStaffMenuItem(ActionMenuItem):  # type: ignore[misc]  # Wagtail is untyped
    """A link in the page editor's action menu to one staff action's confirmation page."""

    def __init__(self, action: str, label: Any, icon_name: str, order: int) -> None:
        super().__init__(order=order)
        self.action = action
        self.label = label
        self.name = f"action-events-{action}"
        self.icon_name = icon_name

    def is_shown(self, context: dict[str, Any]) -> bool:
        event = _event(context.get("page")) if context.get("view") == "edit" else None
        if event is None or not may_moderate_events(context["request"].user):
            return False
        return self.action in {name for name, _label, _icon in _staff_actions(event)}

    def get_url(self, parent_context: dict[str, Any]) -> str:
        return reverse(f"events_admin:{self.action}", args=(parent_context["page"].pk,))


@hooks.register("register_page_action_menu_item")  # type: ignore[untyped-decorator]
def hide_event_menu_item() -> ActionMenuItem:
    return EventStaffMenuItem("hide", _("Hide event"), "no-view", order=70)


@hooks.register("register_page_action_menu_item")  # type: ignore[untyped-decorator]
def restore_event_menu_item() -> ActionMenuItem:
    return EventStaffMenuItem("restore", _("Restore event"), "view", order=71)


@hooks.register("register_page_action_menu_item")  # type: ignore[untyped-decorator]
def cancel_event_menu_item() -> ActionMenuItem:
    return EventStaffMenuItem("cancel", _("Cancel event"), "cross", order=72)


@hooks.register("register_page_listing_more_buttons")  # type: ignore[untyped-decorator]
def event_staff_listing_buttons(
    page: Any, user: Any, next_url: str | None = None
) -> Iterator[Button]:
    event = _event(page)
    if event is None or not may_moderate_events(user):
        return
    for priority, (action, label, icon) in enumerate(_staff_actions(event), start=60):
        url = reverse(f"events_admin:{action}", args=(event.pk,))
        yield Button(label, url, icon_name=icon, priority=priority)


# --- Admin URLs and the "Events" menu


@hooks.register("register_admin_urls")  # type: ignore[untyped-decorator]
def event_admin_urls() -> list[URLPattern | URLResolver]:
    patterns = [
        path("<int:page_id>/hide/", admin_views.hide, name="hide"),
        path("<int:page_id>/restore/", admin_views.restore, name="restore"),
        path("<int:page_id>/cancel/", admin_views.cancel, name="cancel"),
        path("review/", admin_views.review, name="review"),
        path("policies/", admin_views.policies, name="policies"),
        path("policies/set/", admin_views.policy_set, name="policy_set"),
        path("settings/", admin_views.event_settings, name="settings"),
    ]
    return [path("events/", include((patterns, "events_admin"), namespace="events_admin"))]


class _ReviewMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return may_review_events(request.user)


class _StaffMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return may_moderate_events(request.user)


@hooks.register("register_admin_menu_item")  # type: ignore[untyped-decorator]
def events_menu() -> MenuItem:
    menu = Menu(
        items=[
            _ReviewMenuItem(
                _("Awaiting review"), reverse_lazy("events_admin:review"), icon_name="tasks"
            ),
            _StaffMenuItem(_("Settings"), reverse_lazy("events_admin:settings"), icon_name="cog"),
        ]
    )
    return SubmenuMenuItem(_("Events"), menu, name="events", icon_name="date", order=320)
