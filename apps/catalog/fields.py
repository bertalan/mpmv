"""Catalog model fields."""

from typing import Any

from django.db import models

from . import engine


class SkuField(models.CharField[Any, Any]):
    """A SKU, stored stripped and upper case (`engine.canonical_sku`).

    Values are normalized when forms clean them, when instances are saved, and in lookups and
    queryset updates, so `filter(sku="abc-1")` finds "ABC-1". A database check constraint on
    each table refuses anything else (raw SQL). Emptiness is left to `blank=False` and that
    constraint.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", engine.SKU_MAX_LENGTH)
        kwargs.setdefault("verbose_name", "SKU")
        super().__init__(*args, **kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        return engine.canonical_sku(value) if isinstance(value, str) else value

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        return engine.canonical_sku(value) if isinstance(value, str) else value

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        value = getattr(model_instance, self.attname)
        if isinstance(value, str):
            value = engine.canonical_sku(value)
            setattr(model_instance, self.attname, value)
        return value
