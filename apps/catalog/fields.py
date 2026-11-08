"""Catalog model fields."""

from typing import Any

from django import forms
from django.db import models

from . import engine


class SkuField(models.CharField[Any, Any]):
    """A vendor SKU, stored exactly as entered (CAT.13, owner decisions Q-30 and Q-33).

    Forms do not strip it: surrounding spaces are kept and only raise a warning
    (`engine.sku_warnings`). Uniqueness and matching use the model's `sku_key`
    (`engine.sku_match_key`), never this raw label.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", engine.SKU_MAX_LENGTH)
        kwargs.setdefault("verbose_name", "SKU")
        super().__init__(*args, **kwargs)

    def formfield(self, **kwargs: Any) -> forms.Field | None:
        kwargs.setdefault("strip", False)
        return super().formfield(**kwargs)
