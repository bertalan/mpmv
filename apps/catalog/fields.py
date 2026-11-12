"""Catalog model fields."""

from typing import Any

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import LanguageField
from apps.core.models import TranslationStatus

from . import engine


def language_field() -> LanguageField:
    """The `language` of a catalog translation row, with the help staff see in the admin."""
    return LanguageField(
        help_text=_(
            "The language this text is written in. Required: choose one language for each "
            "text; a language can be used only once for the same item."
        )
    )


def translation_status_field() -> "models.CharField[Any, Any]":
    """The review `status` of a catalog translation row, with its help."""
    return models.CharField(
        max_length=16,
        choices=TranslationStatus.choices,
        default=TranslationStatus.MACHINE,
        help_text=_(
            "Where the text is in its review. A machine draft needs a person to read it; "
            "reviewed means it was checked; only a published text is shown to buyers."
        ),
    )


def seo_title_field() -> "models.CharField[Any, Any]":
    return models.CharField(
        "SEO title",
        max_length=255,
        blank=True,
        help_text=_(
            "The title you want search engines to show for this page, ideally under 60 "
            "characters. Optional: you can leave it empty."
        ),
    )


def seo_description_field() -> "models.TextField[Any, Any]":
    return models.TextField(
        "SEO description",
        blank=True,
        help_text=_(
            "The short text you want search engines to show under the title, ideally under 160 "
            "characters. Optional: you can leave it empty."
        ),
    )


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
