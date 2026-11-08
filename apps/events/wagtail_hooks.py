"""Wagtail admin hooks of the events app (EVT.1 part 2/2): thin, the rules are in `services`.

- Vendors author events in the vendor area, never in the Wagtail admin (spec 15). The
  `apps.vendors.backends` backend refuses them the whole admin; these hooks refuse them every
  event page action as a second line (create, edit, delete, unpublish, move, copy, bulk).
- Only platform staff create events in the admin, by adding or by copying a page (spec 15
  "Permissions": only platform staff create platform events).
"""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from wagtail import hooks

from .models import EventPage
from .services import may_create_events_in_admin, may_edit_events_in_admin


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


@hooks.register("before_edit_page")  # type: ignore[untyped-decorator]
def refuse_vendors_editing_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page)


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
