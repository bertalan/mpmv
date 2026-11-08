"""Signal adapters (EVT.1): thin, the work is in `services`."""

from typing import Any

from django.db import DEFAULT_DB_ALIAS, connections
from wagtail.models import Page, WorkflowState

from apps.cms.models import HomePage

from . import services
from .models import EventIndexPage, EventPage


def add_indexes_after_migrate(using: str = DEFAULT_DB_ALIAS, **kwargs: Any) -> None:
    """`post_migrate`: every enabled language's home page gets its events index.

    Only on the default database, and only once the events tables exist (a partial migrate to
    an older target may not have created them).
    """
    if using != DEFAULT_DB_ALIAS:
        return
    if EventIndexPage._meta.db_table in connections[using].introspection.table_names():
        services.ensure_event_indexes()


def add_index_to_published_home(sender: type[HomePage], instance: HomePage, **kwargs: Any) -> None:
    """`page_published` of a home page (the seed, a new language): add its events index."""
    if kwargs.get("alias") or instance.alias_of_id is not None:
        return
    services.ensure_event_indexes()


def sync_status_of_page(sender: type[EventPage], instance: EventPage, **kwargs: Any) -> None:
    """`page_published` and `page_unpublished` of an event page."""
    services.sync_event_status(instance)


def sync_status_of_saved_page(
    sender: type[EventPage], instance: EventPage, raw: bool = False, **kwargs: Any
) -> None:
    """`post_save` of an event page: a copy (for translation) carries the source's status."""
    if not raw:
        services.sync_event_status(instance)


def sync_status_of_workflow(
    sender: type[WorkflowState], instance: WorkflowState, **kwargs: Any
) -> None:
    """Workflow submitted, approved, rejected, cancelled or saved (a resume): sync the page."""
    if kwargs.get("raw"):
        return
    page = instance.content_object
    if isinstance(page, Page):
        page = page.specific
    if isinstance(page, EventPage):
        services.sync_event_status(page)
