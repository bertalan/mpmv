"""Product slugs and the paths of catalog pages (spec 03 §3.2, CAT.5, CAT.8). Pure functions,
no ORM.

A product slug is the transliterated title plus the language, `{title}-{lang}`, at most 80
characters. It replicates the legacy permalink rules without the SKU part (owner decisions Q-30,
Q-33): the vendor SKU never appears in a URL. The legacy `{title}-{lang}-{sku}` slugs live on as
`SlugHistory` and answer a single 301 to the current slug.
"""

import re
import unicodedata
from collections.abc import Container, Iterable

SLUG_MAX_LENGTH = 80
FALLBACK_TITLE = "product"

# Letters that Unicode decomposition does not turn into ASCII (as the legacy `remove_accents`).
_TRANSLITERATION = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "SS",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
        "ł": "l",
        "Ł": "L",
        "\u0131": "i",  # dotless i
    }
)
_APOSTROPHES = re.compile("['\u2018\u2019`]")  # straight, curly and grave apostrophes
_NOT_SLUG = re.compile(r"[^a-z0-9]+")

# The path segment of product pages per language (spec 03 §3.2 URLs). Languages without their
# own segment use the English one.
PRODUCT_SEGMENTS = {"en": "product", "it": "prodotto"}
_SLUG = re.compile(r"^[-a-zA-Z0-9_]+$")  # what a Django SlugField accepts


def _slugify(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text.translate(_TRANSLITERATION))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return _NOT_SLUG.sub("-", _APOSTROPHES.sub("", ascii_text).lower()).strip("-")


def _compose(title_part: str, tail: str, max_len: int) -> str:
    room = max_len - len(tail)
    if room < 1:
        raise ValueError(f"max_len={max_len} leaves no room for the title before {tail!r}")
    return f"{title_part[:room].rstrip('-') or FALLBACK_TITLE[:room]}{tail}"


def build_product_slug(title: str, lang: str, max_len: int = SLUG_MAX_LENGTH) -> str:
    """slugify(translit(title)) truncated + f"-{lang}"; at most `max_len`; never the SKU."""
    return _compose(_slugify(title) or FALLBACK_TITLE, f"-{lang.lower()}", max_len)


def unique_product_slug(
    title: str, lang: str, taken: Container[str], max_len: int = SLUG_MAX_LENGTH
) -> str:
    """`build_product_slug`, with -2, -3… appended while the slug is in `taken`."""
    title_part = _slugify(title) or FALLBACK_TITLE
    slug = _compose(title_part, f"-{lang.lower()}", max_len)
    number = 1
    while slug in taken:
        number += 1
        slug = _compose(title_part, f"-{lang.lower()}-{number}", max_len)
    return slug


def is_slug(value: str, *, max_length: int = SLUG_MAX_LENGTH) -> bool:
    """Whether `value` is a slug that a SlugField of `max_length` accepts."""
    return len(value) <= max_length and bool(_SLUG.match(value))


def product_path(lang: str, slug: str, *, default_language: str) -> str:
    """The path of a product page: `/product/{slug}/`, `/it/prodotto/{slug}/`…"""
    segment = PRODUCT_SEGMENTS.get(lang, PRODUCT_SEGMENTS["en"])
    return f"{language_prefix(lang, default_language=default_language)}/{segment}/{slug}/"


# --- Listing pages (CAT.8, D-07). A taxonomy page has no fixed segment: its first segment is the
# taxonomy's slug in the page language, which can never be one of the fixed segments.
CATEGORY_SEGMENTS = {"en": "product-category", "it": "categoria-prodotto"}
BRAND_SEGMENTS = {"en": "brand", "it": "marchio"}
SHOP_SEGMENTS = {"en": "shop"}
RESERVED_SEGMENTS = frozenset(
    segment
    for segments in (PRODUCT_SEGMENTS, CATEGORY_SEGMENTS, BRAND_SEGMENTS, SHOP_SEGMENTS)
    for segment in segments.values()
)


def language_prefix(lang: str, *, default_language: str) -> str:
    """`""` for the primary language, `/it` and so on for the others (I18N.1)."""
    return "" if lang == default_language else f"/{lang}"


def segment_for(segments: dict[str, str], lang: str) -> str:
    """The fixed path segment of a page type in `lang`; EN when the language has none."""
    return segments.get(lang, segments["en"])


def _listing_path(lang: str, parts: Iterable[str], *, default_language: str) -> str:
    return f"{language_prefix(lang, default_language=default_language)}/{'/'.join(parts)}/"


def category_path(lang: str, slugs: Iterable[str], *, default_language: str) -> str:
    """`/product-category/{parent}/{child}/`, `/it/categoria-prodotto/…`."""
    parts = (segment_for(CATEGORY_SEGMENTS, lang), *slugs)
    return _listing_path(lang, parts, default_language=default_language)


def term_path(lang: str, taxonomy: str, slugs: Iterable[str], *, default_language: str) -> str:
    """`/{taxonomy}/{term}/` and `/{taxonomy}/{term}/{child}/` (D-07), slugs of `lang`."""
    return _listing_path(lang, (taxonomy, *slugs), default_language=default_language)


def brand_path(lang: str, slug: str, *, default_language: str) -> str:
    """`/brand/{slug}/`, `/it/marchio/{slug}/`."""
    parts = (segment_for(BRAND_SEGMENTS, lang), slug)
    return _listing_path(lang, parts, default_language=default_language)


def vendor_path(lang: str, slug: str, *, default_language: str) -> str:
    """`/shop/{vendor}/` in every language (the vendor slug is not translated)."""
    parts = (segment_for(SHOP_SEGMENTS, lang), slug)
    return _listing_path(lang, parts, default_language=default_language)


def parse_product_path(
    path: str, *, languages: Iterable[str], default_language: str
) -> tuple[str, str] | None:
    """The (language, slug) of a product page path; None for any other path."""
    if not path.endswith("/"):
        return None
    parts = path.strip("/").split("/")
    if len(parts) == 2:
        lang = default_language
        segment, slug = parts
    elif len(parts) == 3 and parts[0] != default_language and parts[0] in set(languages):
        lang, segment, slug = parts
    else:
        return None
    if segment != PRODUCT_SEGMENTS.get(lang, PRODUCT_SEGMENTS["en"]) or not _SLUG.match(slug):
        return None
    return lang, slug
