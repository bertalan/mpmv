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
_STRING_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
_SEGMENT_RE = re.compile(r"[^{};]*[{};]")
_CLASS_RE = re.compile(r"\.((?:[\w-]|\\.)+)")
_ESCAPE_RE = re.compile(r"\\(.)")


def class_names(css: str) -> set[str]:
    """Every class named in the selectors of `css` (rule preludes; at-rule preludes skipped)."""
    text = _STRING_RE.sub('""', _COMMENT_RE.sub("", css))
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
        "ride ships no carousel markup and maps nothing for it; the base rule wins if one is added"
    ),
    "pagination": _MAPPED,
    "table": _MAPPED,
    "toast": _MAPPED,
    "visually-hidden": (
        "the same clip-based utility in both: the layers are equal in effect, so the later base "
        "rule changes nothing visible and keeps the helper without Bootstrap"
    ),
}
