"""I18N.15: language helpers for tests, so no test assumes `en` or `it` is configured.

The suite runs with the default languages and also with another `MPMV_LANGUAGES` (CI job
`it,de`). Tests ask for "the primary language" and "a second language" instead of naming them.
"""

import pytest
from django.conf import settings
from django.utils import translation

from apps.catalog import slugs
from apps.core.i18n import configured_languages, primary_language


def other_language() -> str:
    """The first configured language that is not the primary one (`it`, or `de` with it,de)."""
    for code in configured_languages():
        if code != primary_language():
            return code
    pytest.skip("needs a second configured language")


def third_language() -> str:
    """The first configured language that is neither the primary nor `other_language()`."""
    taken = {primary_language(), other_language()}
    for code in configured_languages():
        if code not in taken:
            return code
    pytest.skip("needs a third configured language")


def translated(msgid: str, lang: str | None = None) -> str:
    """`msgid` translated into `lang` (default: the primary language), as a page in it shows it."""
    with translation.override(lang or primary_language()):
        return translation.gettext(msgid)


def language_prefix(code: str) -> str:
    """The URL prefix of a language: empty for the primary one, `/<code>` for the others."""
    return "" if code == settings.LANGUAGE_CODE else f"/{code}"


def product_url(lang: str, slug: str) -> str:
    """The path of a product page in `lang`, whatever the primary language is."""
    return slugs.product_path(lang, slug, default_language=settings.LANGUAGE_CODE)


def category_url(lang: str, *parts: str) -> str:
    """The path of a category page in `lang`."""
    return slugs.category_path(lang, parts, default_language=settings.LANGUAGE_CODE)


def brand_url(lang: str, slug: str) -> str:
    """The path of a brand page in `lang`."""
    return slugs.brand_path(lang, slug, default_language=settings.LANGUAGE_CODE)


def vendor_url(lang: str, slug: str) -> str:
    """The path of a vendor storefront in `lang`."""
    return slugs.vendor_path(lang, slug, default_language=settings.LANGUAGE_CODE)


def localized(path: str, lang: str | None = None) -> str:
    """A catalog page path written with its English segment, in `lang` (default: the primary).

    `/brand/acme/` becomes `/marchio/acme/` when the language is Italian, and gets the language
    prefix when `lang` is not the primary one. Other paths only get the prefix.
    """
    lang = lang or primary_language()
    first, _, rest = path.lstrip("/").partition("/")
    for segments in (
        slugs.PRODUCT_SEGMENTS,
        slugs.CATEGORY_SEGMENTS,
        slugs.BRAND_SEGMENTS,
        slugs.SHOP_SEGMENTS,
    ):
        if first == segments["en"]:
            first = slugs.segment_for(segments, lang)
            break
    return f"{language_prefix(lang)}/{first}/{rest}"
