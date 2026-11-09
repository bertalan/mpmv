"""SVG logos and icons, checked at upload (THM.12, spec 14 §14.7).

SVG is accepted only for logos and icons (vendor and brand logos), in the "Logos and icons"
collection. `sanitise_svg` is an allow-list check, not a cleaner: a document with anything off
the list is refused with the reason, so an editor re-exports a plain SVG instead of getting a
silently altered logo. What passes is re-serialised, which drops comments and the XML
declaration; that output is what gets stored.

Refused: a DTD, entities or external references (defusedxml), processing instructions (such as
`xml-stylesheet`), any element outside the SVG namespace or the element allow-list (so no
`script`, `foreignObject`, `a`, `image`, `feImage`, animation elements), any attribute outside
the attribute allow-list (so no `on*` event handlers), an `href` that is not a same-document
fragment, a `url()` to anything but a fragment, and in CSS `@import`, `@font-face`, escapes,
`image-set()`, `image()`, `src()`, `cross-fade()`, `element()`, `expression()` and any quoted
string that looks like a URL (a scheme or `//`).

Pure Python, no Django: unit-tested on a corpus of malicious documents.
"""

import re
import xml.etree.ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import XMLParser

# The name the data migrations give the collection SVG uploads must go to. Uploads are checked
# against `apps.core.models.SvgCollection`, never against this name.
LOGO_COLLECTION_NAME = "Logos and icons"

MAX_SVG_BYTES = 512 * 1024
# Deepest element nesting accepted. A logo needs a handful of levels; the limit keeps the
# recursive serialiser far from Python's recursion limit.
MAX_SVG_DEPTH = 64

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "title", "desc", "symbol", "use", "style",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan",
        "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern",
        "filter", "feBlend", "feColorMatrix", "feComposite", "feDropShadow", "feFlood",
        "feGaussianBlur", "feMerge", "feMergeNode", "feMorphology", "feOffset",
    }
)  # fmt: skip

ATTRIBUTES = frozenset(
    {
        # Structure, identity and accessibility.
        "id", "class", "style", "version", "baseProfile", "viewBox", "preserveAspectRatio",
        "role", "aria-label", "aria-hidden", "aria-labelledby", "aria-describedby", "lang",
        "focusable", "href",
        # Geometry.
        "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "fr",
        "width", "height", "d", "points", "transform", "pathLength", "dx", "dy", "rotate",
        "textLength", "lengthAdjust",
        # Painting.
        "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap",
        "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
        "stroke-opacity", "opacity", "color", "display", "visibility", "overflow",
        "clip-path", "clip-rule", "mask", "filter", "vector-effect", "shape-rendering",
        "paint-order", "mix-blend-mode", "isolation", "color-interpolation-filters",
        # Text.
        "font-family", "font-size", "font-weight", "font-style", "font-variant",
        "text-anchor", "dominant-baseline", "alignment-baseline", "letter-spacing",
        "word-spacing", "text-decoration", "writing-mode",
        # Gradients, patterns, clips, masks.
        "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform",
        "spreadMethod", "patternUnits", "patternContentUnits", "patternTransform",
        "clipPathUnits", "maskUnits", "maskContentUnits",
        # Filters.
        "filterUnits", "primitiveUnits", "in", "in2", "result", "stdDeviation", "mode",
        "values", "type", "operator", "k1", "k2", "k3", "k4", "flood-color", "flood-opacity",
        "radius", "edgeMode",
        # <style>.
        "media",
    }
)  # fmt: skip

NAMESPACED_ATTRIBUTES = frozenset(
    {f"{{{XLINK_NS}}}href", f"{{{XML_NS}}}space", f"{{{XML_NS}}}lang"}
)

_FRAGMENT = re.compile(r"#[A-Za-z_][\w.:-]*")
# Where a `url(` starts, with any spacing before the parenthesis (which is refused). Scanned with
# `str.find` from there, never with a lazy `.*?` that rescans the rest of the value for every
# unclosed `url(` (quadratic: lane-b second security review of THM.12).
_URL_OPEN = re.compile(r"url\s*\(", re.IGNORECASE)
# The most `url(` references one attribute value or one stylesheet may hold; a logo needs a few.
MAX_URLS_PER_VALUE = 256
_CSS_FORBIDDEN = re.compile(
    r"@import|@font-face|@namespace|image-set|expression\s*\(|javascript:|behavior\s*:|\\"
    # Functions that load a resource from a URL given as a string, or paint another element.
    r"|(?<!\w)(?:image|src|cross-fade|element)\s*\(",
    re.IGNORECASE,
)
# A quoted CSS string: a URL in one (a scheme or `//`) is refused whatever function takes it.
# A negated class instead of a lazy `.*?`: same matches, and only the last unclosed quote of each
# kind scans to the end of the stylesheet.
_CSS_STRING = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_URL_LIKE = re.compile(r"//|^\s*[a-z][a-z0-9+.-]*:", re.IGNORECASE)


class UnsafeSvgError(ValueError):
    """The document is not an allowed SVG; the message says why, for the editor."""


class _TreeBuilder(ET.TreeBuilder):
    """Keeps comments (stripped later), refuses every processing instruction, including
    those before the root element, which a plain TreeBuilder drops without a word, and refuses
    nesting deeper than `MAX_SVG_DEPTH`."""

    def __init__(self) -> None:
        super().__init__(insert_comments=True)
        self._depth = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self._depth += 1
        if self._depth > MAX_SVG_DEPTH:
            raise UnsafeSvgError(f"The SVG is nested more than {MAX_SVG_DEPTH} levels deep.")
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        self._depth -= 1
        return super().end(tag)

    def pi(self, target: str, text: str | None = None) -> ET.Element:
        raise UnsafeSvgError(f"The processing instruction <?{target}?> is refused.")


def _local(tag: str) -> tuple[str, str]:
    """`{namespace}name` split into (namespace, name); no namespace gives ("", name)."""
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return namespace, name
    return "", tag


def _check_urls(value: str, where: str) -> None:
    """Every `url(...)` in `value` must be `url(#fragment)`, optionally quoted and spaced.

    One pass: each `url(` is read up to the next `)`. A `url(` with no `)`, with spacing before
    the parenthesis or with a quote that is not closed right before the `)` cannot be checked and
    is refused, as is a value with more than `MAX_URLS_PER_VALUE` of them."""
    opens = list(_URL_OPEN.finditer(value))
    if len(opens) > MAX_URLS_PER_VALUE:
        raise UnsafeSvgError(f"{where} has more than {MAX_URLS_PER_VALUE} url() references.")
    for match in opens:
        close = value.find(")", match.end())
        if match.group(0).lower() != "url(" or close == -1:
            raise UnsafeSvgError(f"{where} has a url() that cannot be checked.")
        target = value[match.end() : close].strip()
        if target[:1] in ("'", '"'):
            if len(target) < 2 or target[-1] != target[0]:
                raise UnsafeSvgError(f"{where} has a url() that cannot be checked.")
            target = target[1:-1].strip()
        if not _FRAGMENT.fullmatch(target):
            raise UnsafeSvgError(f"{where} references {target!r}: only #fragments are allowed.")


def _check_css(css: str, where: str) -> None:
    forbidden = _CSS_FORBIDDEN.search(css)
    if forbidden:
        raise UnsafeSvgError(f"{where} uses {forbidden.group(0)!r}, which is not allowed.")
    for match in _CSS_STRING.finditer(css):
        if _URL_LIKE.search(match.group(1) or match.group(2) or ""):
            raise UnsafeSvgError(f"{where} has the string {match.group(0)!r}, a URL.")
    _check_urls(css, where)


def _check_element(element: ET.Element) -> None:
    tag = element.tag
    if not isinstance(tag, str):  # a comment node, removed before serialising
        return
    namespace, name = _local(tag)
    if namespace != SVG_NS:
        raise UnsafeSvgError(f"<{name}> is not an SVG element.")
    if name not in ELEMENTS:
        raise UnsafeSvgError(f"<{name}> is not allowed in a logo (for example script).")
    for attribute, value in element.attrib.items():
        attr_ns, attr_name = _local(attribute)
        if attr_name.lower().startswith("on"):
            raise UnsafeSvgError(f"The event handler {attr_name} is refused.")
        if attr_ns:
            if attribute not in NAMESPACED_ATTRIBUTES:
                raise UnsafeSvgError(f"The attribute {attr_name} is not allowed.")
        elif attr_name not in ATTRIBUTES:
            raise UnsafeSvgError(f"The attribute {attr_name} on <{name}> is not allowed.")
        if attr_name == "href" and not _FRAGMENT.fullmatch(value.strip()):
            raise UnsafeSvgError(f"href={value!r}: only #fragments are allowed.")
        if attr_name == "style":
            _check_css(value, f"The style of <{name}>")
        else:
            _check_urls(value, f"The attribute {attr_name}")
            if "javascript:" in value.lower():
                raise UnsafeSvgError(f"The attribute {attr_name} holds a javascript: URL.")
    if name == "style":
        _check_css(element.text or "", "<style>")
        if len(element):
            raise UnsafeSvgError("<style> may contain only CSS.")


def _strip_comments(root: ET.Element) -> None:
    """Drops every comment node in one pass over the tree, keeping the text that followed each
    comment in order. Each element's children are rebuilt once, never removed one by one."""
    stack = [root]
    while stack:
        element = stack.pop()
        kept: list[ET.Element] = []
        # The text pieces that end up before the next kept child (or at the end).
        pieces = [element.text or ""]
        dropped = False
        for child in element:
            if isinstance(child.tag, str):
                _join_text(element, kept, pieces)
                kept.append(child)
                pieces = [child.tail or ""]
            else:  # a comment
                dropped = True
                pieces.append(child.tail or "")
        _join_text(element, kept, pieces)
        if dropped:
            element[:] = kept
        stack.extend(kept)


def _join_text(element: ET.Element, kept: list[ET.Element], pieces: list[str]) -> None:
    text = "".join(pieces) or None
    if kept:
        kept[-1].tail = text
    else:
        element.text = text


def _serialise(root: ET.Element) -> bytes:
    """The tree with the SVG namespace as the default and `xlink:` for XLink."""
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True).encode()


def sanitise_svg(data: bytes) -> bytes:
    """The re-serialised document if it passes the allow-list; `UnsafeSvgError` otherwise."""
    if len(data) > MAX_SVG_BYTES:
        raise UnsafeSvgError(f"The SVG is too large (over {MAX_SVG_BYTES // 1024} KB).")
    try:
        return _sanitise(data)
    except UnsafeSvgError:
        raise
    except DefusedXmlException as error:
        raise UnsafeSvgError(
            "DTDs, entities and external references are refused in SVG."
        ) from error
    except ET.ParseError as error:
        raise UnsafeSvgError(f"The file is not well-formed XML: {error}.") from error
    except LookupError as error:  # an unknown encoding in the XML declaration
        raise UnsafeSvgError(f"The file's encoding is not supported: {error}.") from error
    except (RecursionError, ValueError) as error:  # never a 500 for an odd document
        raise UnsafeSvgError(f"The SVG cannot be read: {error}.") from error


def _sanitise(data: bytes) -> bytes:
    parser = XMLParser(target=_TreeBuilder(), forbid_dtd=True, forbid_entities=True)
    parser.feed(data)
    root = parser.close()
    if not isinstance(root.tag, str) or root.tag != f"{{{SVG_NS}}}svg":
        raise UnsafeSvgError("The document is not an SVG: its root must be an SVG <svg>.")
    for element in root.iter():
        _check_element(element)
    _strip_comments(root)
    return _serialise(root)
