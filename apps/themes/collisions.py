"""Class names that `base` CSS and Bootstrap both define (THM.13, spec 14 §14.5).

In the `ride` chain `base`'s `components` layer sits above Bootstrap's layer, so a class that
both define would be restyled silently. `class_names` reads the classes of every selector of a
stylesheet; `colliding_classes` is the intersection of two stylesheets; `ALLOWED_COLLISIONS`
lists each accepted overlap with its reason, and a unit test fails on any other overlap and on
an entry that no longer collides.
"""

import re
from collections.abc import Mapping

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_QUOTE_RE = re.compile(r"[\"']")
_STRING_RES = {
    '"': re.compile(r"\"(?:[^\"\\\n]|\\.)*\""),
    "'": re.compile(r"'(?:[^'\\\n]|\\.)*'"),
}
# A segment starts at the beginning or right after a delimiter, never in the middle of one.
_SEGMENT_RE = re.compile(r"(?<![^{};])[^{};]*[{};]")
_CLASS_RE = re.compile(r"\.((?:[\w-]|\\.)+)")
_ESCAPE_RE = re.compile(r"\\(.)")


def _blank_strings(text: str) -> str:
    """`text` with every closed CSS string (`'...'` or `"..."`, escapes, one line) emptied to `""`.

    An unclosed quote is left as it is. If a quote finds no end, no later quote of the same kind
    on that line has one either, so the scan skips them and every character is read once."""
    parts: list[str] = []
    copied = 0
    position = 0
    dead = {'"': -1, "'": -1}  # quotes before this index are known to be unclosed
    while quote := _QUOTE_RE.search(text, position):
        start = quote.start()
        kind = text[start]
        position = start + 1
        if start < dead[kind]:
            continue
        string = _STRING_RES[kind].match(text, start)
        if string is None:
            line_end = text.find("\n", start)
            dead[kind] = len(text) if line_end == -1 else line_end
            continue
        parts += [text[copied:start], '""']
        copied = position = string.end()
    parts.append(text[copied:])
    return "".join(parts)


def class_names(css: str) -> set[str]:
    """Every class named in the selectors of `css` (rule preludes; at-rule preludes skipped)."""
    text = _blank_strings(_COMMENT_RE.sub("", css))
    names: set[str] = set()
    for segment in _SEGMENT_RE.findall(text):
        if not segment.endswith("{"):
            continue
        prelude = segment[:-1].strip()
        if prelude.startswith("@"):
            continue
        for match in _CLASS_RE.finditer(prelude):
            name = _ESCAPE_RE.sub(r"\1", match.group(1))
            if not name[0].isdigit():
                names.add(name)
    return names


def colliding_classes(base_css: str, bootstrap_css: str) -> set[str]:
    return class_names(base_css) & class_names(bootstrap_css)


# Accepted overlaps: class -> why `base` and Bootstrap may both style it.
_MAPPED = (
    "base keeps its own markup and rules, in a later layer than Bootstrap's, so the base look wins "
    "on the properties it sets; ride maps the Bootstrap variables of this component on the same "
    "tokens (40-bootstrap-components.css), so a Bootstrap-classed element shows the same look"
)
ALLOWED_COLLISIONS: Mapping[str, str] = {
    "accordion": _MAPPED,
    "alert": _MAPPED,
    "badge": _MAPPED,
    "breadcrumb": _MAPPED,
    "card": _MAPPED,
    "carousel": (
        "ride's Bootstrap carousels (hero slider, strip, gallery) carry `.carousel` too, so base's "
        "scroll-snap grid is reset for them in themes/ride/static/themes/ride/css/35-carousels.css"
    ),
    "pagination": _MAPPED,
    "table": _MAPPED,
    "toast": _MAPPED,
    "visually-hidden": (
        "the same clip-based utility in both: the layers are equal in effect, so the later base "
        "rule changes nothing visible and keeps the helper without Bootstrap"
    ),
}
