"""Site languages from `MPMV_LANGUAGES` (spec 09, I18N.15, D-18).

An ordered list of language codes: the first is the primary (source) language, served without a
URL prefix; the others get `/<code>/`. The list feeds `LANGUAGES`, `LANGUAGE_CODE` and
`WAGTAIL_CONTENT_LANGUAGES`. The default is English first, then Italian, German, French and
Spanish. This module is imported by `base.py` and imports nothing from Django or the apps.

Unknown codes are not rejected here: a settings module that raises hides the reason behind a
traceback, so the code is kept (named by itself) and the `core.E911` system check refuses it.
"""

from collections.abc import Sequence

# Languages the product knows by name (in their own language). A code outside this table is
# refused by core.E911; adding a language to the product means adding it here and a catalog.
KNOWN_LANGUAGES: dict[str, str] = {
    "en": "English",
    "it": "Italiano",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "pt": "Português",
    "nl": "Nederlands",
    "pl": "Polski",
    "ro": "Română",
    "sv": "Svenska",
    "da": "Dansk",
    "fi": "Suomi",
    "el": "Ελληνικά",
    "cs": "Čeština",
    "hu": "Magyar",
    "sk": "Slovenčina",
    "sl": "Slovenščina",
    "hr": "Hrvatski",
    "bg": "Български",
}

DEFAULT_LANGUAGE_CODES: tuple[str, ...] = ("en", "it", "de", "fr", "es")


def language_settings(codes: Sequence[str]) -> tuple[str, list[tuple[str, str]]]:
    """`(LANGUAGE_CODE, LANGUAGES)` for the codes in `MPMV_LANGUAGES`; empty means the default."""
    chosen = [code.strip().lower() for code in codes if code.strip()] or list(
        DEFAULT_LANGUAGE_CODES
    )
    return chosen[0], [(code, KNOWN_LANGUAGES.get(code, code)) for code in chosen]
