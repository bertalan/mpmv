"""The SEO settings of each Wagtail Site (SEO.8 2/2, spec 10, owner decision D-35)."""

from django.db import models
from django.utils.translation import gettext_lazy as _
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.models import Site


class SeoSettings(BaseSiteSetting):  # type: ignore[misc]  # Wagtail is untyped
    """One row per Wagtail Site. Written only through `services.set_site_indexing` (audited).

    `allow_indexing` is off until staff turn it on, so a new site is noindex. It can only add
    noindex on top of the environment floor (`MPMV_FORCE_NOINDEX`, demo mode): under the floor
    it can be stored off, never on. Do not read it with Wagtail's `for_site`, which creates the
    row on first access, outside the audit trail: use `current`.
    """

    # Editable (Wagtail's is not), so the panel can pick the site.
    site = models.OneToOneField(Site, on_delete=models.CASCADE, verbose_name=_("site"))
    allow_indexing = models.BooleanField(
        _("allow search engines to index this site"),
        default=False,
        help_text=_(
            "Off (the default): search engines are told not to index any page of this site. "
            "On: pages can appear in search results. It has no effect while the server "
            "forces noindex (STAGE or demo)."
        ),
    )

    class Meta:
        verbose_name = _("SEO settings")
        verbose_name_plural = _("SEO settings")

    def __str__(self) -> str:
        return f"{self.site.hostname}: indexing {'on' if self.allow_indexing else 'off'}"

    @classmethod
    def current(cls, site: Site) -> "SeoSettings":
        """The settings of `site`, or unsaved defaults: a read never creates the row."""
        found: SeoSettings | None = cls.objects.filter(site=site).first()
        return found if found is not None else cls(site=site)
