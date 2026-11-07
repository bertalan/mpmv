"""Language-prefix routing helpers (spec 09 §9.2, I18N.1).

`/` is the primary language (settings.LANGUAGE_CODE, EN) with no prefix; every other language in
settings.LANGUAGES gets `/<code>/`. Nothing here lists languages: adding one is a settings entry.
"""

from django.conf import settings
from django.urls import translate_url
from django.utils import translation


def language_choices() -> list[tuple[str, str]]:
    """`choices` for language fields, read from settings when used (I18N.2).

    Fields pass this callable, not the list: migrations then reference it by name, so adding a
    language to settings.LANGUAGES needs no migration (`apps.core.fields.LanguageField`).
    """
    return [(str(code), str(name)) for code, name in settings.LANGUAGES]


def configured_languages() -> list[str]:
    """Language codes in the configured order (primary first)."""
    return [code for code, _name in settings.LANGUAGES]


def translate_path(path: str, language: str) -> str:
    """The same page in another configured language; unchanged if the path is not translatable.

    Django's `translate_url` only resolves paths in the active language, so the path is resolved
    in its own language first (the prefix it carries, or the primary language when it has none).
    Query string and fragment are kept; routes outside `i18n_patterns` are returned as they are.
    """
    if language not in configured_languages():
        return path
    source = translation.get_language_from_path(path) or settings.LANGUAGE_CODE
    with translation.override(source):
        return translate_url(path, language)
