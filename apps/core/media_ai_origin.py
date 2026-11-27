"""Who changes a library image's AI origin, as the Wagtail image form sees it (CMP.8).

The rule and its audit trail live in the catalog's `media.set_media_ai_origin` (editors raise,
platform staff lower, every change audited). `apps.core` sits below the catalog and cannot import
it, so the catalog registers its two functions at start-up (`CatalogConfig.ready`) and the form
calls them through here.
"""

from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured

_setter: Callable[..., Any] | None = None
_may_lower: Callable[[Any], bool] | None = None


def register(*, setter: Callable[..., Any], may_lower: Callable[[Any], bool]) -> None:
    global _setter, _may_lower
    _setter, _may_lower = setter, may_lower


def may_lower(user: Any) -> bool:
    if _may_lower is None:
        raise ImproperlyConfigured("No AI origin rule is registered (apps.catalog).")
    return _may_lower(user)


def set_origin(image: Any, ai_origin: str, user: Any) -> None:
    """Change `image`'s origin through the audited service, as `user`."""
    if _setter is None:
        raise ImproperlyConfigured("No AI origin rule is registered (apps.catalog).")
    from .audit import user_context

    _setter(image, ai_origin, ctx=user_context(user, None))
