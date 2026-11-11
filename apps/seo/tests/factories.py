"""Factories for the SEO app's models."""

from typing import Any

from factory.django import DjangoModelFactory
from wagtail.models import Site

from apps.seo.models import SeoSettings


class SeoSettingsFactory(DjangoModelFactory[SeoSettings]):
    """The SEO settings of the default Wagtail site, indexing off unless asked."""

    class Meta:
        model = SeoSettings

    site = None
    allow_indexing = False

    @classmethod
    def _create(cls, model_class: type[SeoSettings], *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("site") is None:
            kwargs["site"] = Site.objects.get(is_default_site=True)
        return model_class._default_manager.create(*args, **kwargs)
