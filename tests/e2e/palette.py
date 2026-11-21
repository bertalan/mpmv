"""Choose a brand palette for the e2e site (THM.31, spec 14 §14.12), as an editor does.

The palette goes through the audited service on the default Wagtail site, so the page links the
hashed `/palette/` stylesheet after `theme.css`.
"""

from playwright.sync_api import Page
from wagtail.models import Site

from apps.core.audit import AuditContext
from apps.themes.models import BrandPalette
from apps.themes.palettes import PRESETS
from apps.themes.services import clear_caches, set_site_theme

PALETTES = ("blue", "petrol")
PALETTE_LINK = 'link[rel="stylesheet"][href^="/palette/"]'
CTX = AuditContext(actor_label="system:e2e", source="system")


def _defaults(slug: str) -> dict[str, str]:
    """The preset `slug` as model field values; fails loudly when the model and PRESETS drift."""
    colours = {
        f.name for f in BrandPalette._meta.concrete_fields if f.name not in ("id", "slug", "name")
    }
    preset = PRESETS[slug]
    missing = colours - preset.keys()
    extra = preset.keys() - colours - {"name"}
    if missing or extra:
        raise AssertionError(
            f"BrandPalette and PRESETS drifted: no preset value for {sorted(missing)}, "
            f"no model field for {sorted(extra)}"
        )
    return {key: preset[key] for key in (*sorted(colours), "name")}


def assert_palette_applied(page: Page, slug: str) -> None:
    """The page's computed accent is the palette's primary, so a silent fallback to Red fails."""
    accent = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim()"
    )
    assert accent.upper() == PRESETS[slug]["primary"].upper(), (
        f"palette {slug}: computed --color-accent is {accent!r}, "
        f"expected {PRESETS[slug]['primary']}"
    )


def choose_palette(slug: str) -> None:
    """Switch the default site to `ride` in the preset palette `slug`.

    A transactional e2e test starts with the data migration's rows flushed, so the preset is
    created here the way the migration seeds it.
    """
    site = Site.objects.get(is_default_site=True)
    palette, _ = BrandPalette.objects.update_or_create(slug=slug, defaults=_defaults(slug))
    set_site_theme(site, "ride", palette=palette, ctx=CTX)
    clear_caches()
