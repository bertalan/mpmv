"""The theme of each Wagtail Site (spec 14 §14.3, Selection)."""

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.models import Site

from .palettes import ROLES, check_brand_palette


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
    site = models.OneToOneField(
        Site,
        on_delete=models.CASCADE,
        verbose_name="site",
        help_text=_("The site this theme is for. Each site has one theme."),
    )
    theme = models.CharField(
        max_length=32,
        blank=True,
        help_text=_(
            "The name of an installed theme. Leave it empty to use the default theme of the"
            " environment."
        ),
    )

    class Meta:
        verbose_name = "site theme"
        verbose_name_plural = "site themes"

    def __str__(self) -> str:
        return f"{self.site.hostname}: {self.theme or 'environment default'}"


HEX_COLOUR = RegexValidator(
    r"^#[0-9A-Fa-f]{6}$", "Use a six digit hex colour such as #C40417.", code="hex_colour"
)


def _colour(help_text: str) -> "models.CharField[str, str]":
    return models.CharField(max_length=7, validators=[HEX_COLOUR], help_text=help_text)


class BrandPalette(models.Model):
    """A site colour palette (spec 14 §14.12, THM.31): data, not code.

    The Red, Blue and Petrol presets are seeded by a data migration. Every row passes
    `check_brand_palette` (`clean`), whatever staff type. Roles map to tokens in
    `palettes.ROLE_TOKENS`.
    """

    slug = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    primary = _colour("Buttons, badges, links and UI colour.")
    primary_hover = _colour("Hover and pressed background of the primary.")
    primary_tint = _colour("Tinted section background; the primary is text on it.")
    on_primary = _colour("Text on the primary and its hover.")
    primary_dark = _colour("The primary on the dark surface #121417.")
    secondary = _colour("Accent background, always with text.")
    secondary_hover = _colour("Hover background of the accent.")
    secondary_text = _colour("The accent used as text or icon on the surface.")
    on_secondary = _colour("Text on the accent and its hover.")
    band = _colour("Header band, footer and dark sections.")
    text_on_band = _colour("Text and links on the band.")
    focus = _colour("Focus ring, 3:1 on the surface and the band.")
    control_border = _colour("Borders of inputs and outlined buttons, 3:1 on the surface.")

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def roles(self) -> dict[str, str]:
        return {role: getattr(self, role) for role in ROLES}

    def violations(self) -> list[str]:
        """Failing pairs against the surface of every registered theme (empty when valid)."""
        from .services import registered_surfaces  # models are imported before the registry

        return [str(v) for v in check_brand_palette(self.roles(), registered_surfaces())]

    def clean(self) -> None:
        problems = self.violations()
        if problems:
            raise ValidationError({"primary": problems})
