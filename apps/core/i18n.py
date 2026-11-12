"""Language-prefix routing helpers (spec 09 §9.2, I18N.1).

`/` is the primary language (settings.LANGUAGE_CODE, the first of MPMV_LANGUAGES) with no
prefix; every other language in
settings.LANGUAGES gets `/<code>/`. Nothing here lists languages: adding one is a settings entry.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, cast

from django.conf import settings
from django.http import HttpRequest
from django.urls import translate_url
from django.utils import translation


def language_choices() -> list[tuple[str, str]]:
    """`choices` for language fields, read from settings when used (I18N.2).

    Fields pass this callable, not the list: migrations then reference it by name, so adding a
    language to settings.LANGUAGES needs no migration (`apps.core.fields.LanguageField`).
    """
    return [(str(code), str(name)) for code, name in settings.LANGUAGES]


def primary_language() -> str:
    """The primary language, read from settings when called.

    Model fields use this callable as `default`: a literal `settings.LANGUAGE_CODE` would be
    frozen at import and migrations would drift when MPMV_LANGUAGES changes.
    """
    return str(settings.LANGUAGE_CODE)


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


@dataclass(frozen=True)
class LanguageLink:
    """One entry of the language switcher."""

    code: str
    name: str  # in its own language ("Italiano")
    url: str  # where the switch leads: the object in that language, else its home
    current: bool
    available: bool  # False: the object has no translation, `url` is the home (notice shown)


def hreflang_alternates(urls: Mapping[str, str]) -> list[tuple[str, str]]:
    """The `hreflang` set of an object from its `{language: URL}` of existing translations, in
    the configured order, with `x-default` on the source language's URL (spec 09, I18N.6).

    The language switcher reads the same mapping (`switcher_links`): its translation targets and
    the alternates cannot drift apart.
    """
    alternates = [(code, urls[code]) for code in configured_languages() if code in urls]
    source = settings.LANGUAGE_CODE
    alternates.extend(("x-default", url) for code, url in list(alternates) if code == source)
    return alternates


def switcher_links(
    urls: Mapping[str, str], current: str, home: Callable[[str], str]
) -> list[LanguageLink]:
    """One switcher entry per configured language for an object with translations `urls`
    (`{language: URL}`, the mapping of `hreflang_alternates`): the translation where there is
    one, otherwise `home(language)`, flagged unavailable so the switch shows a notice."""
    links = []
    for code in configured_languages():
        url = urls.get(code)
        links.append(
            LanguageLink(
                code=code,
                name=str(translation.get_language_info(code)["name_local"]),
                url=url if url is not None else home(code),
                current=code == current,
                available=url is not None,
            )
        )
    return links


def language_switch_home[V: Callable[..., Any]](view: V) -> V:
    """Mark a view whose URL carries a secret (action link, application key, claim key): its
    language switcher leads to the other language's home and never writes the URL, with its
    token, into the page (crawlers, Referer). No notice: there is no object to translate."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        request.language_switch_home = True  # type: ignore[attr-defined]
        return view(request, *args, **kwargs)

    wrapper.language_switch_home = True  # type: ignore[attr-defined]  # marker for the URL guard
    return cast(V, wrapper)


def switch_goes_home(request: HttpRequest | None) -> bool:
    return bool(getattr(request, "language_switch_home", False))
