"""Cache generation of a vendor's closure notice (VEN.20), the pattern of `cms.snippet_cache`.

A page or fragment cache that shows the vendor's notice (SEO.6's anonymous page cache: the
storefront, the product pages and the cart of the vendor) puts `generation(vendor_id)` in its key.
It moves when the vendor's schedule changes, after the change is committed. The notice also
changes with the calendar, so such a cache must expire no later than the notice's first or last
day (spec 04): that part belongs to the page cache itself.

The counter lives in the default cache. With the per-process LocMem backend a bump in one worker
is not seen by the others, so SEO.6 must run on a shared backend before it reads this.
"""

from django.core.cache import cache
from django.db import transaction


def _key(vendor_id: int) -> str:
    return f"vendors:closures:generation:{vendor_id}"


def generation(vendor_id: int) -> int:
    return int(cache.get_or_set(_key(vendor_id), 1, None) or 1)


def drop_closure_cache(vendor_id: int) -> None:
    """Start a new generation for the vendor once the surrounding transaction commits."""

    def _bump() -> None:
        generation(vendor_id)  # makes sure the key exists
        cache.incr(_key(vendor_id))

    transaction.on_commit(_bump)
