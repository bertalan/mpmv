"""System checks for the TranslationModel reuse contract (I18N.2).

Registered in `CoreConfig.ready`, so `manage.py check` (and `make check`) fails when a
`*Translation` table, e.g. a new catalog one, does not follow the contract.
"""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from .models import TranslationModel

# Workflow fields of the base model: never translated texts.
BASE_FIELDS = frozenset(f.name for f in TranslationModel._meta.get_fields())


def _field(model: type[models.Model], name: str) -> Any:
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _is_text(model: type[models.Model], name: str) -> bool:
    return name not in BASE_FIELDS and isinstance(
        _field(model, name), models.CharField | models.TextField
    )


def check_translation_model(model: type[TranslationModel]) -> list[checks.CheckMessage]:
    label = model._meta.label
    parent = model.translation_of
    texts = model.translated_fields
    constraints = model._meta.constraints
    errors: list[checks.CheckMessage] = []

    if not parent or not isinstance(_field(model, parent), models.ForeignKey):
        errors.append(
            checks.Error(
                f"{label}.translation_of must name a ForeignKey of the model (got {parent!r}).",
                obj=model,
                id="core.E101",
            )
        )

    if not texts or not all(_is_text(model, name) for name in texts):
        errors.append(
            checks.Error(
                f"{label}.translated_fields must list the model's own CharField or TextField "
                f"texts (got {texts!r}).",
                obj=model,
                id="core.E102",
            )
        )

    if not any(
        isinstance(c, models.UniqueConstraint)
        and set(c.fields) == {parent, "language"}
        and c.condition is None
        for c in constraints
    ):
        errors.append(
            checks.Error(
                f"{label} needs a UniqueConstraint on ({parent!r}, 'language'): one row per "
                "translated entity and language.",
                obj=model,
                id="core.E103",
            )
        )

    status_name = f"{model._meta.app_label}_{model._meta.model_name}_status_valid"
    if not any(
        isinstance(c, models.CheckConstraint) and c.name == status_name for c in constraints
    ):
        errors.append(
            checks.Error(
                f"{label} lost the status check constraint: declare "
                "`class Meta(TranslationModel.Meta)` and keep "
                "`*TranslationModel.Meta.constraints`.",
                obj=model,
                id="core.E104",
            )
        )
    return errors


def translation_models(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Check every concrete TranslationModel subclass of the given apps (default: all)."""
    if app_configs is None:
        candidates: Iterable[type[models.Model]] = apps.get_models()
    else:
        candidates = [model for config in app_configs for model in config.get_models()]
    errors: list[checks.CheckMessage] = []
    for model in candidates:
        if issubclass(model, TranslationModel):
            errors.extend(check_translation_model(model))
    return errors
