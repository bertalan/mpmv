"""Pure CMS rules (no ORM): the gallery replacement rule of D-18 (I18N.3, spec 09 §9.2).

An image is language neutral unless it is dedicated to one language. A language with at least
one dedicated image shows only those; any other language shows the neutral images. Product
galleries (CAT.4) follow the same rule.
"""

from collections.abc import Iterable, Mapping
from typing import Any

NEUTRAL = ""  # the `language` of an image shown in every language without dedicated images


def gallery_for_language[T: Mapping[str, Any]](items: Iterable[T], language: str) -> list[T]:
    """The items to show in `language`, in their order. Each item has a `language` key."""
    wanted = language.lower()
    items = list(items)
    dedicated = [item for item in items if item["language"] and item["language"].lower() == wanted]
    if dedicated:
        return dedicated
    return [item for item in items if not item["language"]]
