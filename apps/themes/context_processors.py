"""Template context of the active theme (THM.1, THM.3)."""

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from .services import active_theme


def theme(request: HttpRequest) -> dict[str, Any]:
    """`theme`: the request's `ThemeManifest` (`theme.slug`, `theme.name`), looked up on use;
    `help_url`: the help/contact link of the header and footer (`MPMV_HELP_URL`).

    Layouts put `data-theme="{{ theme.slug }}"` on `<html>` and link
    `themes/<slug>/theme.css`.
    """
    return {
        "theme": SimpleLazyObject(lambda: active_theme(request)),
        "help_url": settings.MPMV_HELP_URL,
    }
