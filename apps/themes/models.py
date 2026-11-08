"""The theme of each Wagtail Site (spec 14 §14.3, Selection)."""

from django.db import models
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.models import Site


class ThemeSettings(BaseSiteSetting):  # type: ignore[misc]  # Wagtail is untyped
    """One theme per Wagtail Site. Written only through `services.set_site_theme` (audited).

    `theme` is a registered theme slug, or empty for the environment default
    (`MPMV_THEME_DEFAULT`). It is not a database choice field: themes are directories, so
    adding one needs no migration; the admin form and the service validate the slug against
    the registry, and the themes.E003 check reports a stored slug whose theme was removed.

    Do not read it with Wagtail's `for_site` or `for_request`: they create a row on first
    access, outside the audit trail. `services.site_theme_choice` reads without writing.
    """

    # Editable (Wagtail's is not), so staff pick the site on the add form.
    site = models.OneToOneField(Site, on_delete=models.CASCADE, verbose_name="site")
    theme = models.CharField(
        max_length=32,
        blank=True,
        help_text="Registered theme slug; empty uses the environment default.",
    )

    class Meta:
        verbose_name = "site theme"
        verbose_name_plural = "site themes"

    def __str__(self) -> str:
        return f"{self.site.hostname}: {self.theme or 'environment default'}"
