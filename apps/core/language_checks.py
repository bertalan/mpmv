"""System checks for the site languages (I18N.15, spec 09).

- core.E911: a language code the product does not know (`mpmv.settings.languages`).
- core.E912: a language listed twice.
- core.E913: LANGUAGE_CODE is not the first language (the primary language is the first one).
- core.W911 (core.I911 when informational): the gettext catalog of a non-English language is
  missing or incomplete. The msgids
  are English, so every gap would show English text on that language's pages. English needs no
  catalog. German, French and Spanish are still waiting for their catalogs (I18N.7): for them the
  same finding is only informational, so the shipped default configuration stays free of
  warnings until I18N.7 delivers; remove `CATALOG_PENDING` then.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polib
from django.apps import AppConfig
from django.conf import settings
from django.core import checks

from mpmv.settings.languages import KNOWN_LANGUAGES

# Languages in the default set whose interface catalogs are delivered by I18N.7.
CATALOG_PENDING = frozenset({"de", "fr", "es"})
# The language the msgids are written in: it needs no catalog.
SOURCE_LANGUAGE = "en"


def _catalog_gap(code: str) -> tuple[int, int] | None:
    """`(missing, total)` of the first catalog found for `code`; None when no catalog exists."""
    for root in settings.LOCALE_PATHS:
        path = Path(root) / code.replace("-", "_") / "LC_MESSAGES" / "django.po"
        if path.is_file():
            entries = [entry for entry in polib.pofile(str(path)) if not entry.obsolete]
            missing = [entry for entry in entries if not entry.translated()]
            return len(missing), len(entries)
    return None


def check_language_settings(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    messages: list[checks.CheckMessage] = []
    codes = [str(code) for code, _name in settings.LANGUAGES]
    for code in sorted({c for c in codes if c not in KNOWN_LANGUAGES}):
        messages.append(
            checks.Error(
                f"MPMV_LANGUAGES: unknown language code {code!r}.",
                hint="Known codes: " + ", ".join(KNOWN_LANGUAGES) + ".",
                id="core.E911",
            )
        )
    for code in sorted({c for c in codes if codes.count(c) > 1}):
        messages.append(checks.Error(f"MPMV_LANGUAGES: {code!r} is listed twice.", id="core.E912"))
    if codes and codes[0] != settings.LANGUAGE_CODE:
        messages.append(
            checks.Error(
                f"LANGUAGE_CODE {settings.LANGUAGE_CODE!r} is not the first language "
                f"({codes[0]!r}): the primary language is the first of MPMV_LANGUAGES.",
                id="core.E913",
            )
        )
    for code in dict.fromkeys(codes):
        if code == SOURCE_LANGUAGE or code not in KNOWN_LANGUAGES:
            continue
        gap = _catalog_gap(code)
        if gap is None:
            problem = "has no interface catalog"
        elif gap[0]:
            problem = f"has an incomplete interface catalog ({gap[0]} of {gap[1]} untranslated)"
        else:
            continue
        message = (
            f"Language {code!r} {problem}: its pages would show English text where it is missing."
        )
        if code in CATALOG_PENDING:
            messages.append(checks.Info(message, hint="Delivered by I18N.7.", id="core.I911"))
        else:
            messages.append(
                checks.Warning(
                    message,
                    hint=f"Translate locale/{code}/LC_MESSAGES/django.po, then compilemessages.",
                    id="core.W911",
                )
            )
    return messages
