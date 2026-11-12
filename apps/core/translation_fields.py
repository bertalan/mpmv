"""The `language` and `status` fields of a `TranslationModel`, with their help text (ARC.31).

A translation table redeclares both fields with these factories so the Django admin shows the
help; the fields are otherwise the ones `TranslationModel` declares.
"""

from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from .fields import LanguageField
from .models import TranslationStatus


def translation_language_field() -> LanguageField:
    return LanguageField(
        help_text=_(
            "The language of this translation. Each language has one row for the same item."
        )
    )


def translation_status_field() -> "models.CharField[Any, Any]":
    return models.CharField(
        max_length=16,
        choices=TranslationStatus.choices,
        default=TranslationStatus.MACHINE,
        help_text=_(
            "'Machine draft' was made automatically and needs a person to check it. "
            "'Reviewed' was checked by a person. 'Published' is shown to visitors. If the "
            "English text changes, reviewed and published translations go back to 'Machine "
            "draft'."
        ),
    )
