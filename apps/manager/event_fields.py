"""Form fields and labels of the vendor's event form (EVT.4): the map's coordinate field, the
wall clock date and time, and the labels of the product and category choosers."""

import datetime as dt
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Category, Product
from apps.core.geocoding import engine as geo_engine

_TIME_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")


def product_label(product: Product) -> str:
    """The product's title in the active language (else any), with its SKU."""
    texts = {t.language: t.title for t in product.translations.all() if t.title}
    title = texts.get(get_language() or "en") or texts.get("en") or next(iter(texts.values()), "")
    return f"{title} ({product.sku})" if title else str(product.sku)


def category_label(category: Category) -> str:
    names = {t.language: t.name for t in category.translations.all() if t.name}
    name = names.get(get_language() or "en") or names.get("en") or next(iter(names.values()), "")
    return str(name or _("Category %(number)d") % {"number": category.pk})


class CoordinateField(forms.CharField):
    """A latitude or longitude typed by hand or set by the map: six decimal places."""

    def __init__(self, *, limit: int, **kwargs: Any) -> None:
        super().__init__(required=False, max_length=20, **kwargs)
        self.limit = limit

    def clean(self, value: Any) -> Any:
        text = super().clean(value)
        if not text:
            return None
        try:
            return geo_engine.coordinate(text.strip().replace(",", "."), limit=self.limit)
        except ValueError:
            raise ValidationError(
                _("Give a number from -%(limit)d to %(limit)d, for example 45.464200."),
                params={"limit": self.limit},
            ) from None


class WallClockField(forms.DateTimeField):
    """A date and time kept as typed (naive): the form reads it in the event's own zone."""

    widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")

    def to_python(self, value: Any) -> dt.datetime | None:
        if value in self.empty_values:
            return None
        if isinstance(value, dt.datetime):
            return value.replace(tzinfo=None)
        for fmt in _TIME_FORMATS:
            try:
                return dt.datetime.strptime(str(value).strip(), fmt)
            except ValueError:
                continue
        raise ValidationError(self.error_messages["invalid"], code="invalid")
