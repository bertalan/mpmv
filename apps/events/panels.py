"""Wagtail admin panels of event pages (EVT.1 part 2/2, spec 15 "Staff admin").

The edit view groups the fields as the spec does: when, where (with the coordinates and the
geocoding metadata, read-only), attendance, links, body, and a moderation panel shown to staff
only. The geocoding metadata and the moderation state are written by services (EVT.3, EVT.12),
never by the form: read-only panels add no form field, so a crafted request cannot set them.

The "Where" panel carries the map picker (EVT.13, spec 15 "Map picker"): a container whose data
attributes name the form's inputs, the "Find coordinates" endpoint and the tile settings, read by
the shared `events/map.js` module with the vendored Leaflet. No inline script, no third-party
host: `wagtailgeowidget` was evaluated and not used (docs/dev/maps.md).
"""

from typing import Any

from django.conf import settings
from django.forms import Media, Script
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import (
    FieldPanel,
    FieldRowPanel,
    MultiFieldPanel,
    Panel,
    TitleFieldPanel,
)

from apps.vendors.scoping import is_platform_staff

# The inputs of the event form the picker reads and writes (`data-<name>-input` on the picker).
PICKER_INPUTS = ("latitude", "longitude", "street", "postcode", "city", "country")
# The hidden inputs that keep the chosen "Find coordinates" candidate's point when the marker is
# moved before saving (`data-picked-latitude-input`, `data-picked-longitude-input`).
PICKED_INPUTS = ("picked_latitude", "picked_longitude")


def map_assets() -> Media:
    """Leaflet (vendored, THM.3) and the shared map module, for every page with a map."""
    # Leaflet first: the module reads the global `L` (modules run after classic scripts).
    scripts: list[Any] = ["vendor/leaflet/leaflet.js", Script("events/map.js", type="module")]
    return Media(css={"all": ["vendor/leaflet/leaflet.css", "events/map.css"]}, js=scripts)


def map_settings() -> dict[str, Any]:
    """The tile layer and the view without a point, as the map module's data attributes read
    them (`data-tile-url`, `data-attribution`, ...)."""
    latitude, longitude, zoom = settings.MAP_DEFAULT_VIEW
    return {
        "tile_url": settings.MAP_TILE_URL,
        "attribution": settings.MAP_TILE_ATTRIBUTION,
        "attribution_url": settings.MAP_TILE_ATTRIBUTION_URL,
        "default_latitude": latitude,
        "default_longitude": longitude,
        "default_zoom": zoom,
    }


class MapPickerPanel(Panel):  # type: ignore[misc]  # Wagtail is untyped
    """The map, "Find coordinates" and its candidate list, above the latitude and longitude.

    It adds no form field: the picker writes the `latitude`, `longitude` and `geo_source` inputs
    of their own panels, which stay the keyboard and no-JavaScript alternative.
    """

    class BoundPanel(Panel.BoundPanel):  # type: ignore[misc]
        template_name = "events/admin/map_picker_panel.html"

        @property
        def media(self) -> Media:
            return map_assets()

        def get_context_data(self, parent_context: Any = None) -> dict[str, Any]:
            context: dict[str, Any] = super().get_context_data(parent_context)
            form = self.form
            inputs = {name: form[name].auto_id for name in PICKER_INPUTS if name in form.fields}
            locale = getattr(self.instance, "locale", None)
            # The chosen candidate's point (EVT.13): hidden fields of the form, which Wagtail
            # renders with the form's other hidden fields; the picker only needs their ids.
            picked = [form[name] for name in PICKED_INPUTS if name in form.fields]
            context.update(
                inputs=inputs,
                picked_inputs={field.name.replace("_", "-"): field.auto_id for field in picked},
                source_input=form["geo_source"].auto_id if "geo_source" in form.fields else "",
                find_url=reverse("geocoding_find"),
                language=getattr(locale, "language_code", ""),
                help_id=f"{self.prefix}-help",
                **map_settings(),
            )
            return context


class StaffOnlyPanel(MultiFieldPanel):  # type: ignore[misc]  # Wagtail is untyped
    """A group shown to platform staff only (`view_all_vendors`), not to other Wagtail editors."""

    class BoundPanel(MultiFieldPanel.BoundPanel):  # type: ignore[misc]
        def is_shown(self) -> bool:
            user = getattr(self.request, "user", None)
            return user is not None and is_platform_staff(user) and bool(super().is_shown())


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
                MapPickerPanel(heading=_("Map")),
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
