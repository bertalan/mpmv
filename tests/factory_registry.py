"""Find the project's models and their factory_boy factories (ARC.12, ADR-0006).

Each app keeps its factories in `apps/<app>/tests/factories.py`, one `DjangoModelFactory` per
concrete model. Used by the root conftest (reseeding) and by the coverage tests.
"""

import functools
import importlib
from typing import Any

from django.apps import apps
from django.db import models
from factory.django import DjangoModelFactory

Factory = type[DjangoModelFactory[Any]]


def project_models() -> list[type[models.Model]]:
    """Concrete, managed models defined in `apps.*` (auto-created M2M tables excluded)."""
    return [
        model
        for model in apps.get_models()
        if model.__module__.startswith("apps.") and model._meta.managed and not model._meta.proxy
    ]


@functools.cache
def app_factories() -> tuple[Factory, ...]:
    """Every DjangoModelFactory defined in an `apps.<app>.tests.factories` module."""
    found: list[Factory] = []
    for config in apps.get_app_configs():
        if not config.name.startswith("apps."):
            continue
        module_name = f"{config.name}.tests.factories"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name is not None and module_name.startswith(exc.name):
                continue  # the app has no tests package or no factories module yet
            raise
        found.extend(
            obj
            for obj in vars(module).values()
            if isinstance(obj, type)
            and issubclass(obj, DjangoModelFactory)
            and obj.__module__ == module_name
        )
    return tuple(found)


def factory_model(factory_class: Factory) -> type[models.Model]:
    model: type[models.Model] = factory_class._meta.model
    return model
