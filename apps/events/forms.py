"""The Wagtail admin form of an event page (EVT.1 part 2/2, spec 15 "Staff admin").

- **Times in the event's time zone.** Staff type the local time of the event: the admin's own
  time zone (the user's profile, UTC by default) would shift it. The form shows `starts_at` and
  `ends_at` in the event's zone and reads what is typed in the zone chosen on the form.
- **Vendor.** Only staff choose the vendor, and only when the event is created: the owner of an
  existing event never changes here (the same rule as a product's vendor, Q-33). Only staff
  create events in the admin (`wagtail_hooks`); a non-staff editor edits existing ones only.
- **Language.** A new event's original language is the language of its tree.
- **Products.** The chooser offers only the event vendor's products; a crafted request for
  another vendor's product is a field error (spec 15: a vendor event links only to its own
  products).
- **Point (EVT.13).** The map picker fills `latitude`, `longitude` and `geo_source`; the form
  never takes geocoding metadata from the request. A "Find coordinates" candidate is confirmed
  against the cached lookup of the address (`services.resolve_picked_point`) and its provider,
  precision and confidence come from there; no point clears the metadata. A candidate the author
  moves before saving keeps its metadata: the picker sends the chosen candidate's point in the
  hidden `picked_latitude` and `picked_longitude` inputs, checked against the cache the same way.
"""

import datetime as dt
import zoneinfo
from decimal import Decimal
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms import WagtailAdminPageForm

from apps.catalog.models import Product, ProductStatus
from apps.core.geocoding import AddressQuery, GeocodeResult, engine

ADDRESS_QUERY_FIELDS = ("country", "postcode", "city", "street")

LOCAL_TIME_FIELDS = ("starts_at", "ends_at")


def _zone(name: Any) -> zoneinfo.ZoneInfo | None:
    try:
        return zoneinfo.ZoneInfo(str(name))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return None


class EventPageForm(WagtailAdminPageForm):  # type: ignore[misc]  # Wagtail is untyped
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        instance = self.instance
        user = self.for_user
        # A new event is written in the language of its tree (read-only on the form).
        if instance.original_locale_id is None and instance.locale_id is not None:
            instance.original_locale_id = instance.locale_id
        if "vendor" in self.fields:
            if user is None or not getattr(user, "is_staff", False):
                del self.fields["vendor"]
            elif instance.pk is not None:
                self.fields["vendor"].disabled = True
        if "products" in self.fields:
            offered = ~Q(status=ProductStatus.RETIRED)
            if instance.pk is not None:
                # A page built from a revision holds its links in memory (modelcluster): read
                # them as objects, not with `values()`, which such a queryset cannot run.
                offered |= Q(pk__in=[product.pk for product in instance.products.all()])
            products = Product.objects.filter(offered)
            if instance.pk is not None and instance.vendor_id is not None:
                products = products.filter(vendor_id=instance.vendor_id)
            self.fields["products"].queryset = products
        zone = _zone(instance.timezone)
        if zone is not None:
            for name in LOCAL_TIME_FIELDS:
                value = getattr(instance, name, None)
                if name in self.fields and value is not None:
                    self.initial[name] = value.astimezone(zone).replace(tzinfo=None)

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean()
        zone = _zone(cleaned.get("timezone", self.instance.timezone))
        if zone is not None:
            current = timezone.get_current_timezone()
            for name in LOCAL_TIME_FIELDS:
                value = cleaned.get(name)
                if isinstance(value, dt.datetime) and timezone.is_aware(value):
                    local = timezone.make_naive(value, current)
                    cleaned[name] = local.replace(tzinfo=zone)
        vendor_id = self._vendor_id(cleaned)
        products = cleaned.get("products")
        if (
            vendor_id is not None
            and products is not None
            and any(product.vendor_id != vendor_id for product in products)
        ):
            self.add_error("products", _("Choose only products of the event's vendor."))
        self._clean_point(cleaned)
        return cleaned

    # The point of the "Find coordinates" candidate the author chose, kept by the picker when the
    # marker is moved afterwards (EVT.13). Hidden (Wagtail renders the form's hidden fields). A
    # hint only: it is checked against the cache and an unreadable value is ignored.
    picked_latitude = forms.CharField(required=False, widget=forms.HiddenInput)
    picked_longitude = forms.CharField(required=False, widget=forms.HiddenInput)

    _picked_candidate: GeocodeResult | None = None
    _clear_point = False

    def _chosen_point(self, cleaned: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
        try:
            return (
                engine.coordinate(str(cleaned.get("picked_latitude") or "").strip()),
                engine.coordinate(str(cleaned.get("picked_longitude") or "").strip(), limit=180),
            )
        except ValueError:
            return None

    def _clean_point(self, cleaned: dict[str, Any]) -> None:
        # Imported here: the models module imports this form.
        from .services import resolve_picked_point

        self._picked_candidate = None
        self._clear_point = False
        if "geo_source" not in self.fields:
            return
        address = AddressQuery(
            **{name: str(cleaned.get(name) or "") for name in ADDRESS_QUERY_FIELDS}
        )
        try:
            picked = resolve_picked_point(
                self.instance,
                latitude=cleaned.get("latitude"),
                longitude=cleaned.get("longitude"),
                source=str(cleaned.get("geo_source") or ""),
                address=address,
                chosen=self._chosen_point(cleaned),
            )
        except ValidationError as error:
            self.add_error(None, error)
            return
        cleaned["geo_source"] = picked.source
        self._picked_candidate = picked.candidate
        self._clear_point = not picked.source

    def save(self, commit: bool = True) -> Any:
        """Apply the confirmed candidate's metadata, or clear it with the point (EVT.13)."""
        page = self.instance
        if self._clear_point:
            page.clear_point()
        candidate = self._picked_candidate
        if candidate is not None:
            latitude, longitude, source = page.latitude, page.longitude, page.geo_source
            page.set_geocoded_point(candidate, at=timezone.now())
            if source != page.GeoSource.GEOCODED:
                # Chosen, then moved on the map or typed: the moved point keeps its source and
                # the candidate's metadata (spec 15 "Confirmation").
                page.set_point(latitude, longitude, source=source)
        return super().save(commit=commit)

    def _vendor_id(self, cleaned: dict[str, Any]) -> int | None:
        if self.instance.pk is not None or "vendor" not in self.fields:
            vendor_id: int | None = self.instance.vendor_id
            return vendor_id
        vendor = cleaned.get("vendor")
        return vendor.pk if vendor is not None else None
