"""Template context of the active theme (THM.1)."""

from typing import Any

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from .services import active_theme


def theme(request: HttpRequest) -> dict[str, Any]:
    """`theme`: the request's `ThemeManifest` (`theme.slug`, `theme.name`), looked up on use.

    Layouts put `data-theme="{{ theme.slug }}"` on `<html>`; THM.2 links the theme's
    stylesheet from it.
    """
    return {"theme": SimpleLazyObject(lambda: active_theme(request))}
