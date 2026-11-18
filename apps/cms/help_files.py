"""Help content in the repository (ARC.36): the file format, its parser and the placeholders.

A help file is `help/<lang>/<kind>/<stem>.md`. `kind` is `route`, `page_type` or `general`; the
stem is the entry's key with `:` written as `__` (`manager:shop:orders` is
`manager__shop__orders.md`; a page type keeps its case: `cms.BlogPostPage.md`). The general entry
is `help/<lang>/general/general.md`. A file is

    # Title
    last-checked: 2026-11-17
    vendor-guide: yes            (optional; links the vendor guide)

    Markdown body, converted to the CMS.12-sanitised rich text of `HelpEntry`.

Operator-specific facts are never written in a file: the body names an allow-listed placeholder
(`{{ operator.support_email }}`) that is filled at display time. An unknown placeholder is an
error at sync time and in the test suite.
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import markdown  # type: ignore[import-untyped]
from django.conf import settings
from django.utils.html import escape

from . import engine
from .models import HELP_FEATURES

HELP_ROOT = Path(settings.BASE_DIR) / "help"
KINDS = (engine.HELP_ROUTE, engine.HELP_PAGE_TYPE, engine.HELP_GENERAL, engine.HELP_GUIDE)
GENERAL_STEM = "general"
_SEP = "__"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]*)\s*\}\}")
_TITLE = re.compile(r"#\s+(\S.*)")
_META = re.compile(r"([a-z-]+):\s*(.*)")
_HEADING = re.compile(r"<(/?)h[1-3]>")


class HelpFileError(ValueError):
    """A help file that cannot be used; the message names the file."""


@dataclass(frozen=True)
class HelpFile:
    language: str
    kind: str
    key: str  # "" for the general entry
    title: str
    last_checked: dt.date
    vendor_guide: bool
    markdown: str
    path: Path


def key_to_stem(key: str) -> str:
    return key.replace(":", _SEP) if key else GENERAL_STEM


def stem_to_key(kind: str, stem: str) -> str:
    return "" if kind == engine.HELP_GENERAL else stem.replace(_SEP, ":")


# --- Placeholders: an explicit allow-list, nothing else is readable from a help file


def _operator(field: str, fallback: str = "") -> Callable[[], str]:
    """Reads `field` of the current operator; an empty value falls back to the `fallback` field."""

    def read() -> str:
        from apps.core.services import current_operator

        operator = current_operator()
        value = str(getattr(operator, field))
        if not value and fallback:
            value = str(getattr(operator, fallback))
        return value

    return read


def _link(url_name: str) -> Callable[[], str]:
    def read() -> str:
        from django.urls import reverse

        return reverse(url_name)

    return read


PLACEHOLDERS: dict[str, Callable[[], str]] = {
    "operator.legal_name": _operator("legal_name"),
    "operator.support_email": _operator("support_email"),
    "operator.compliance_email": _operator("compliance_email", fallback="support_email"),
    "settings.site_name": lambda: str(settings.WAGTAIL_SITE_NAME),
    # Links to public pages, resolved in the language shown (the pages carry a language prefix).
    "link.help": _link("customer_help"),
    "link.withdrawal": _link("withdrawal"),
    "link.consumer_information": _link("consumer_information"),
    "link.orders": _link("customer_orders"),
    "link.privacy": _link("privacy_account"),
}


def placeholders_in(text: str) -> set[str]:
    return {match.group(1) for match in _PLACEHOLDER.finditer(text)}


def unknown_placeholders(text: str) -> set[str]:
    return placeholders_in(text) - PLACEHOLDERS.keys()


def render_placeholders(html: str) -> str:
    """`html` with each allow-listed placeholder replaced by its current, escaped value. A name
    outside the list, or a value that cannot be read, becomes empty: display never fails."""

    def fill(match: re.Match[str]) -> str:
        read = PLACEHOLDERS.get(match.group(1))
        if read is None:
            return ""
        try:
            return str(escape(read()))
        except Exception:  # no operator yet, for instance
            return ""

    return _PLACEHOLDER.sub(fill, html)


# --- Parsing


def parse(path: Path, root: Path = HELP_ROOT) -> HelpFile:
    rel = path.relative_to(root)
    if len(rel.parts) != 3 or path.suffix != ".md":
        raise HelpFileError(f"{rel}: expected <lang>/<kind>/<key>.md")
    language, kind, _name = rel.parts
    if kind not in KINDS:
        raise HelpFileError(f"{rel}: kind must be one of {', '.join(KINDS)}")
    stem = path.stem
    if kind == engine.HELP_GENERAL and stem != GENERAL_STEM:
        raise HelpFileError(f"{rel}: the general entry is general/general.md")
    key = stem_to_key(kind, stem)
    if kind == engine.HELP_GUIDE:
        if not engine.guide_key_valid(key):
            raise HelpFileError(f"{rel}: {key!r} is not a registered guide section")
    elif kind != engine.HELP_GENERAL and not engine.help_key_valid(key):
        raise HelpFileError(f"{rel}: {key!r} is not a valid route name or page type")
    lines = path.read_text(encoding="utf-8").splitlines()
    title = _TITLE.fullmatch(lines[0]) if lines else None
    if title is None:
        raise HelpFileError(f"{rel}: the first line must be '# Title'")
    meta: dict[str, str] = {}
    index = 1
    while index < len(lines) and lines[index].strip():
        found = _META.fullmatch(lines[index])
        if found is None:
            raise HelpFileError(f"{rel}: line {index + 1} is not 'name: value'")
        meta[found.group(1)] = found.group(2).strip()
        index += 1
    extra = set(meta) - {"last-checked", "vendor-guide"}
    if extra:
        raise HelpFileError(f"{rel}: unknown front matter {sorted(extra)}")
    try:
        checked = dt.date.fromisoformat(meta["last-checked"])
    except (KeyError, ValueError) as exc:
        raise HelpFileError(f"{rel}: 'last-checked: YYYY-MM-DD' is required") from exc
    body = "\n".join(lines[index:]).strip()
    if len(title.group(1)) > 120:
        raise HelpFileError(f"{rel}: the title is longer than 120 characters")
    bad = unknown_placeholders(f"{title.group(1)}\n{body}")
    if bad:
        raise HelpFileError(f"{rel}: unknown placeholder {sorted(bad)}")
    return HelpFile(
        language=language,
        kind=kind,
        key=key,
        title=title.group(1),
        last_checked=checked,
        vendor_guide=meta.get("vendor-guide", "").lower() in {"yes", "true"},
        markdown=body,
        path=path,
    )


def load_all(root: Path = HELP_ROOT) -> list[HelpFile]:
    if not root.is_dir():
        return []
    return [parse(path, root) for path in sorted(root.glob("*/*/*.md"))]


def to_rich_text(text: str) -> str:
    """The Markdown body as sanitised `HelpEntry` rich text (headings become the h4 the editor
    offers; the CMS.12 sanitiser has the last word)."""
    html = markdown.markdown(text, output_format="html")
    return engine.sanitise_rich_text(_HEADING.sub(r"<\1h4>", html), HELP_FEATURES)
