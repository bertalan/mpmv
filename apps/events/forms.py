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
"""

import datetime as dt
import zoneinfo
from typing import Any

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms import WagtailAdminPageForm

from apps.catalog.models import Product, ProductStatus

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
        return cleaned

    def _vendor_id(self, cleaned: dict[str, Any]) -> int | None:
        if self.instance.pk is not None or "vendor" not in self.fields:
            vendor_id: int | None = self.instance.vendor_id
            return vendor_id
        vendor = cleaned.get("vendor")
        return vendor.pk if vendor is not None else None
