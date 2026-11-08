"""Wagtail admin hooks of the events app (EVT.1 part 2/2): thin, the rule is in `services`.

Vendor members are refused on event pages in the Wagtail admin (spec 15: vendors author events
in the vendor area). The rule covers creating, editing and deleting an event page.
"""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from wagtail import hooks

from .models import EventPage
from .services import may_edit_events_in_admin


def _refuse_vendors(request: HttpRequest, page_class: type[Any]) -> None:
    if issubclass(page_class, EventPage) and not may_edit_events_in_admin(request.user):
        raise PermissionDenied


@hooks.register("before_create_page")  # type: ignore[untyped-decorator]
def refuse_vendors_creating_events(
    request: HttpRequest, parent_page: Any, page_class: type[Any]
) -> None:
    _refuse_vendors(request, page_class)


@hooks.register("before_edit_page")  # type: ignore[untyped-decorator]
def refuse_vendors_editing_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page.specific_class or type(page))


@hooks.register("before_delete_page")  # type: ignore[untyped-decorator]
def refuse_vendors_deleting_events(request: HttpRequest, page: Any) -> None:
    _refuse_vendors(request, page.specific_class or type(page))
