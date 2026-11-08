from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save


class EventsConfig(AppConfig):
    """Events: promotions, trade fairs and meetings as Wagtail pages (spec 15)."""

    name = "apps.events"
    label = "events"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). Event pages keep Wagtail's revisions and
    # action log, and the EVT.3 services write an AuditLog entry for every mutation; the policy
    # and the settings are changed by staff through audited services (EVT.3).
    audited_models = ("eventpage", "vendoreventpolicy", "eventsettings")
    unaudited_models = {
        "eventindexpage": "Wagtail page: editor content with revisions and Wagtail's action log",
    }

    def ready(self) -> None:
        from wagtail.models import WorkflowState
        from wagtail.signals import (
            page_published,
            page_unpublished,
            workflow_approved,
            workflow_cancelled,
            workflow_rejected,
            workflow_submitted,
        )

        from apps.cms.models import HomePage

        from . import signals
        from .models import EventPage

        # An events index in every enabled language: after migrate, and when a home page of a
        # new language is published (spec 15 "Languages").
        post_migrate.connect(
            signals.add_indexes_after_migrate, sender=self, dispatch_uid="events.indexes"
        )
        page_published.connect(
            signals.add_index_to_published_home, sender=HomePage, dispatch_uid="events.home_index"
        )
        # The denormalised status follows the Wagtail state (spec 15 "Status").
        for signal, uid in (
            (page_published, "events.status_published"),
            (page_unpublished, "events.status_unpublished"),
        ):
            signal.connect(signals.sync_status_of_page, sender=EventPage, dispatch_uid=uid)
        for signal, uid in (
            (workflow_submitted, "events.status_submitted"),
            (workflow_approved, "events.status_approved"),
            (workflow_rejected, "events.status_rejected"),
            (workflow_cancelled, "events.status_cancelled"),
        ):
            signal.connect(signals.sync_status_of_workflow, sender=WorkflowState, dispatch_uid=uid)
        # Wagtail's "Resubmit" (`WorkflowState.resume`) sends no workflow signal, only a save.
        post_save.connect(
            signals.sync_status_of_workflow, sender=WorkflowState, dispatch_uid="events.status_wf"
        )
        # A new page (also a copy for translation, which publishes nothing) and any later save:
        # the status is the page's own, never one copied from another page.
        post_save.connect(
            signals.sync_status_of_saved_page, sender=EventPage, dispatch_uid="events.status_save"
        )
