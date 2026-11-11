"""robots.txt and the forced-noindex floor (SEO.8, D-35); the rules live in apps.core.indexing."""

from apps.core.indexing import NOINDEX, PRIVATE_PATHS, is_noindex_forced, robots_txt

is_forced = is_noindex_forced

__all__ = ["NOINDEX", "PRIVATE_PATHS", "is_forced", "robots_txt"]
