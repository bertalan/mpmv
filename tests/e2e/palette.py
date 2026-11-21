"""Choose a brand palette for the e2e site (THM.31, spec 14 §14.12), as an editor does.

The palette goes through the audited service on the default Wagtail site, so the page links the
hashed `/palette/` stylesheet after `theme.css`.
"""

from wagtail.models import Site

from apps.core.audit import AuditContext
from apps.themes.models import BrandPalette
from apps.themes.palettes import PRESETS
from apps.themes.services import clear_caches, set_site_theme

PALETTES = ("blue", "petrol")
PALETTE_LINK = 'link[rel="stylesheet"][href^="/palette/"]'
CTX = AuditContext(actor_label="system:e2e", source="system")


def choose_palette(slug: str) -> None:
    """Switch the default site to `ride` in the preset palette `slug`.

    A transactional e2e test starts with the data migration's rows flushed, so the preset is
    created here the way the migration seeds it.
    """
    site = Site.objects.get(is_default_site=True)
    palette, _ = BrandPalette.objects.update_or_create(slug=slug, defaults=PRESETS[slug])
    set_site_theme(site, "ride", palette=palette, ctx=CTX)
    clear_caches()
