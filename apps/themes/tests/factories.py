"""factory_boy factories for the themes app (THM.1). Synthetic values only.

`SiteFactory` makes a Wagtail `Site` with its own hostname. Its root page defaults, on create
only, to the default site's root page (the primary home after the CMS seed), so two sites can
serve the same page tree under two themes; `.build()` stays off the database.
"""

from typing import Any

import factory
from factory.django import DjangoModelFactory
from wagtail.models import Page, Site

from apps.themes.models import ThemeSettings


def _default_root() -> Page:
    site = Site.objects.filter(is_default_site=True).select_related("root_page").first()
    if site is not None:
        return site.root_page
    root: Page = Page.get_first_root_node()
    return root


class SiteFactory(DjangoModelFactory[Site]):
    class Meta:
        model = Site

    hostname = factory.Sequence(lambda n: f"site{n}.example.test")
    port = 80
    site_name = factory.Sequence(lambda n: f"Site {n}")
    is_default_site = False

    @classmethod
    def _create(cls, model_class: type[Site], *args: Any, **kwargs: Any) -> Site:
        if kwargs.get("root_page") is None:
            kwargs["root_page"] = _default_root()
        site = model_class(*args, **kwargs)
        site.save()
        return site


class ThemeSettingsFactory(DjangoModelFactory[ThemeSettings]):
    """A site's theme choice. Written directly: tests of the audited path use the service."""

    class Meta:
        model = ThemeSettings

    site = factory.SubFactory(SiteFactory)
    theme = "base"
