"""Factories for the SEO app's models."""

import uuid
from typing import Any

import factory
from factory.django import DjangoModelFactory
from wagtail.models import Site

from apps.seo import engine
from apps.seo.models import ConsentLog, SeoSettings


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


class ConsentLogFactory(DjangoModelFactory[ConsentLog]):
    """One consent decision: a new random consent id each time, "reject all" by default."""

    class Meta:
        model = ConsentLog

    consent_id = factory.LazyFunction(uuid.uuid4)
    action = engine.REJECT_ALL
    granted: list[str] = []
    banner_version = engine.BANNER_VERSION
    provider = "self-hosted"
    language = "en"
