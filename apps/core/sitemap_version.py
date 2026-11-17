"""The sitemap version (I18N.16): a counter in the cache keys of the sitemaps and `llms.txt`.

The services that change what those files list call `bump_sitemap_version()` (a vendor status
change, a product save or delete, a language change); the next request then builds fresh files
instead of waiting for the cache to expire. The counter lives in the database (one row of
`SiteLanguages`) because the default cache is per process and several workers serve requests.
No model signal: a bulk update does not bump it, the content fingerprint and the short TTL stay
as the fallback.
"""

from django.db import transaction
from django.db.models import F

from .models import SiteLanguages


def sitemap_version() -> int:
    return SiteLanguages.objects.values_list("sitemap_version", flat=True).first() or 0


def bump_sitemap_version() -> None:
    """Add one to the version once the surrounding transaction commits (a rolled-back change
    leaves it alone, and no reader can cache the old state under the new version)."""
    transaction.on_commit(_bump)


def _bump() -> None:
    from .language_services import configured_codes  # imports this module at load time

    if not SiteLanguages.objects.update(sitemap_version=F("sitemap_version") + 1):
        # No row yet: record the configured languages with it, never an empty list.
        SiteLanguages.objects.create(codes=configured_codes(), sitemap_version=1)
