"""Factories for the SEO app's models."""

from typing import Any

from factory.django import DjangoModelFactory
from wagtail.models import Site

from apps.seo.models import SeoSettings


class SeoSettingsFactory(DjangoModelFactory[SeoSettings]):
    """The SEO settings of a new Wagtail site that serves the default site's page tree, indexing
    off unless asked. Pass `site=` to set a given site's."""

    class Meta:
        model = SeoSettings

    site = None
    allow_indexing = False

    @classmethod
    def _create(cls, model_class: type[SeoSettings], *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("site") is None:
            root = Site.objects.get(is_default_site=True).root_page
            number = Site.objects.count()
            kwargs["site"] = Site.objects.create(
                hostname=f"seo{number}.example.test", port=80, root_page=root
            )
        return model_class._default_manager.create(*args, **kwargs)
