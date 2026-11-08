"""Wagtail admin panels of event pages (EVT.1 part 2/2, spec 15 "Staff admin").

The edit view groups the fields as the spec does: when, where (with the coordinates and the
geocoding metadata, read-only), attendance, links, body, and a moderation panel shown to staff
only. The geocoding metadata and the moderation state are written by services (EVT.3, EVT.12),
never by the form: read-only panels add no form field, so a crafted request cannot set them.
The map picker joins the "Where" panel with EVT.13.
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel, FieldRowPanel, MultiFieldPanel, TitleFieldPanel


class StaffOnlyPanel(MultiFieldPanel):  # type: ignore[misc]  # Wagtail is untyped
    """A group shown to platform staff only (`is_staff`), not to other Wagtail editors."""

    class BoundPanel(MultiFieldPanel.BoundPanel):  # type: ignore[misc]
        def is_shown(self) -> bool:
            user = getattr(self.request, "user", None)
            return bool(getattr(user, "is_staff", False)) and bool(super().is_shown())


def _read_only(*names: str) -> list[FieldPanel]:
    return [FieldPanel(name, read_only=True) for name in names]


def event_content_panels() -> list[Any]:
    return [
        TitleFieldPanel("title"),
        FieldPanel("summary"),
        FieldPanel("event_type"),
        MultiFieldPanel(
            [
                FieldPanel("all_day"),
                FieldRowPanel([FieldPanel("starts_at"), FieldPanel("ends_at")]),
                FieldRowPanel([FieldPanel("start_date"), FieldPanel("end_date")]),
                FieldPanel(
                    "timezone",
                    help_text=_("Times above are read in this time zone, e.g. Europe/Rome."),
                ),
                FieldPanel("rrule"),
                FieldPanel("exdates"),
                FieldPanel("exdate_days"),
            ],
            heading=_("When"),
        ),
        MultiFieldPanel(
            [FieldPanel("attendance"), FieldPanel("online_url")],
            heading=_("Attendance"),
        ),
        MultiFieldPanel(
            [
                FieldPanel("venue_name"),
                FieldPanel("street"),
                FieldRowPanel([FieldPanel("postcode"), FieldPanel("city")]),
                FieldRowPanel([FieldPanel("region"), FieldPanel("country")]),
                FieldPanel("booth"),
                FieldRowPanel([FieldPanel("latitude"), FieldPanel("longitude")]),
                FieldPanel("geo_source"),
                *_read_only("geo_provider", "geo_precision", "geo_confidence", "geo_at"),
            ],
            heading=_("Where"),
        ),
        MultiFieldPanel(
            [
                FieldPanel("vendor"),
                FieldPanel("products"),
                FieldPanel("categories"),
                FieldPanel("listing_image"),
            ],
            heading=_("Links"),
        ),
        FieldPanel("body"),
        StaffOnlyPanel(
            _read_only(
                "status",
                "original_locale",
                "cancelled_at",
                "hidden_at",
                "hidden_by",
                "hidden_reason",
                "dsa_notice_ref",
                "sequence",
                "content_changed_at",
            ),
            heading=_("Moderation"),
        ),
    ]
