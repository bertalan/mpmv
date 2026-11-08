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
`image-set()` and `expression()`.

Pure Python, no Django: unit-tested on a corpus of malicious documents.
"""

import re
import xml.etree.ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import XMLParser

# The collection SVG uploads must go to (created by a data migration).
LOGO_COLLECTION_NAME = "Logos and icons"

MAX_SVG_BYTES = 512 * 1024

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
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_CSS_FORBIDDEN = re.compile(
    r"@import|@font-face|@namespace|image-set|expression\s*\(|javascript:|behavior\s*:|\\",
    re.IGNORECASE,
)


class UnsafeSvgError(ValueError):
    """The document is not an allowed SVG; the message says why, for the editor."""


class _TreeBuilder(ET.TreeBuilder):
    """Keeps comments (stripped later) and refuses every processing instruction, including
    those before the root element, which a plain TreeBuilder drops without a word."""

    def __init__(self) -> None:
        super().__init__(insert_comments=True)

    def pi(self, target: str, text: str | None = None) -> ET.Element:
        raise UnsafeSvgError(f"The processing instruction <?{target}?> is refused.")


def _local(tag: str) -> tuple[str, str]:
    """`{namespace}name` split into (namespace, name); no namespace gives ("", name)."""
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return namespace, name
    return "", tag


def _check_urls(value: str, where: str) -> None:
    for match in _URL.finditer(value):
        target = match.group(2).strip()
        if not _FRAGMENT.fullmatch(target):
            raise UnsafeSvgError(f"{where} references {target!r}: only #fragments are allowed.")
    # A `url(` the pattern cannot read (unbalanced, odd spacing) is refused too.
    if len(re.findall(r"url\s*\(", value, re.IGNORECASE)) != len(_URL.findall(value)):
        raise UnsafeSvgError(f"{where} has a url() that cannot be checked.")


def _check_css(css: str, where: str) -> None:
    forbidden = _CSS_FORBIDDEN.search(css)
    if forbidden:
        raise UnsafeSvgError(f"{where} uses {forbidden.group(0)!r}, which is not allowed.")
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


def _strip_comments(element: ET.Element) -> None:
    for child in list(element):
        if not isinstance(child.tag, str):  # a comment
            # Keep the text that followed the comment.
            index = list(element).index(child)
            if child.tail:
                if index == 0:
                    element.text = (element.text or "") + child.tail
                else:
                    previous = element[index - 1]
                    previous.tail = (previous.tail or "") + child.tail
            element.remove(child)
        else:
            _strip_comments(child)


def _serialise(root: ET.Element) -> bytes:
    """The tree with the SVG namespace as the default and `xlink:` for XLink."""
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True).encode()


def sanitise_svg(data: bytes) -> bytes:
    """The re-serialised document if it passes the allow-list; `UnsafeSvgError` otherwise."""
    if len(data) > MAX_SVG_BYTES:
        raise UnsafeSvgError(f"The SVG is too large (over {MAX_SVG_BYTES // 1024} KB).")
    parser = XMLParser(target=_TreeBuilder(), forbid_dtd=True, forbid_entities=True)
    try:
        parser.feed(data)
        root = parser.close()
    except DefusedXmlException as error:
        raise UnsafeSvgError(
            "DTDs, entities and external references are refused in SVG."
        ) from error
    except ET.ParseError as error:
        raise UnsafeSvgError(f"The file is not well-formed XML: {error}.") from error
    if not isinstance(root.tag, str) or root.tag != f"{{{SVG_NS}}}svg":
        raise UnsafeSvgError("The document is not an SVG: its root must be an SVG <svg>.")
    for element in root.iter():
        _check_element(element)
    _strip_comments(root)
    return _serialise(root)
