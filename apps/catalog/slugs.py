"""Product slugs and product paths (spec 03 §3.2, CAT.5). Pure functions, no ORM.

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
    prefix = "" if lang == default_language else f"/{lang}"
    return f"{prefix}/{segment}/{slug}/"


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
