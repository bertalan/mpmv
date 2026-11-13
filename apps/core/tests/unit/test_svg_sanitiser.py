"""THM.12: SVG logos and icons are checked at upload against an allow-list.

Done when: "a scripted SVG is refused". `apps.core.svg.sanitise_svg` parses with defusedxml (no
DTD, no entities, no external references), accepts only SVG elements and attributes on its
allow-lists, refuses scripts, event handlers, `foreignObject`, external references and `url()`
to anything but a same-document fragment, and returns a clean re-serialisation (comments and
processing instructions dropped) that is what gets stored.
"""

import contextlib
import time
from collections.abc import Callable

import pytest
from defusedxml.ElementTree import fromstring

from apps.core.svg import MAX_SVG_BYTES, UnsafeSvgError, sanitise_svg

SVG = 'xmlns="http://www.w3.org/2000/svg"'
XLINK = 'xmlns:xlink="http://www.w3.org/1999/xlink"'

SAFE_LOGO = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- exported by an editor -->
<svg {SVG} {XLINK} viewBox="0 0 160 80" width="160" height="80" role="img" aria-label="Logo">
  <title>Vendor A</title>
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a4"/><stop offset="1" stop-color="#062"/>
    </linearGradient>
    <clipPath id="c"><rect width="160" height="80" rx="8"/></clipPath>
    <filter id="f"><feGaussianBlur stdDeviation="1"/></filter>
    <style>.mark {{ fill: url(#g); }} .text {{ font-family: sans-serif; }}</style>
  </defs>
  <g clip-path="url(#c)" filter="url('#f')">
    <path class="mark" d="M0 0h80v80H0z"/>
    <circle cx="120" cy="40" r="20" fill="url(#g)" style="stroke: #000; stroke-width: 2"/>
    <use xlink:href="#c" href="#c"/>
    <text class="text" x="90" y="75" font-size="12">A</text>
  </g>
</svg>
"""

# Each entry: (name, document). Every one must be refused.
MALICIOUS: list[tuple[str, str]] = [
    ("script element", f"<svg {SVG}><script>alert(1)</script></svg>"),
    ("script with href", f'<svg {SVG} {XLINK}><script xlink:href="https://x.test/a.js"/></svg>'),
    ("onload on root", f'<svg {SVG} onload="alert(1)"><rect width="1" height="1"/></svg>'),
    ("onclick on child", f'<svg {SVG}><rect onclick="alert(1)" width="1" height="1"/></svg>'),
    ("uppercase handler", f'<svg {SVG}><rect ONMOUSEOVER="alert(1)" width="1"/></svg>'),
    (
        "foreignObject",
        f'<svg {SVG}><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">'
        "<img src=x onerror=alert(1)></div></foreignObject></svg>",
    ),
    (
        "html element",
        f'<svg {SVG}><iframe xmlns="http://www.w3.org/1999/xhtml" src="https://x.test"/></svg>',
    ),
    ("javascript href", f'<svg {SVG}><a href="javascript:alert(1)"><text>x</text></a></svg>'),
    (
        "external xlink:href on use",
        f'<svg {SVG} {XLINK}><use xlink:href="https://x.test/sprite.svg#a"/></svg>',
    ),
    ("external href on use", f'<svg {SVG}><use href="http://x.test/s.svg#a"/></svg>'),
    (
        "data href on image",
        f'<svg {SVG} {XLINK}><image xlink:href="data:image/svg+xml;base64,PHN2Zz4="/></svg>',
    ),
    (
        "protocol-relative href",
        f'<svg {SVG}><use href="//x.test/s.svg#a"/></svg>',
    ),
    ("relative file href", f'<svg {SVG}><use href="other.svg#a"/></svg>'),
    ("style url to http", f"<svg {SVG}><style>rect {{ fill: url(http://x.test/a) }}</style></svg>"),
    ("style url to data", f'<svg {SVG}><style>rect {{ fill: url("data:x") }}</style></svg>'),
    ("style import", f'<svg {SVG}><style>@import "https://x.test/a.css";</style></svg>'),
    ("style font-face", f"<svg {SVG}><style>@font-face {{ src: local(x) }}</style></svg>"),
    ("style escape", f"<svg {SVG}><style>rect {{ fill: u\\72l(http://x.test) }}</style></svg>"),
    ("style image-set", f'<svg {SVG}><style>rect {{ fill: image-set("x.png" 1x) }}</style></svg>'),
    ("style image()", f'<svg {SVG}><style>rect{{fill:image("http://e")}}</style></svg>'),
    ("style src()", f'<svg {SVG}><style>rect{{fill:src("http://e")}}</style></svg>'),
    ("style cross-fade()", f"<svg {SVG}><style>rect{{fill:cross-fade(red, blue)}}</style></svg>"),
    ("style element()", f"<svg {SVG}><style>rect{{fill:element(#a)}}</style></svg>"),
    ("style string with scheme", f"<svg {SVG}><style>rect{{fill:foo('data:x')}}</style></svg>"),
    ("style string protocol-relative", f'<svg {SVG}><style>rect{{fill:x("//e/a")}}</style></svg>'),
    (
        "style attribute image()",
        f'<svg {SVG}><rect style="fill: image(&quot;http://e&quot;)" width="1"/></svg>',
    ),
    (
        "style attribute url",
        f'<svg {SVG}><rect style="fill: url(https://x.test/p)" width="1"/></svg>',
    ),
    (
        "presentation attribute url",
        f'<svg {SVG}><rect fill="url(https://x.test/p#g)" width="1"/></svg>',
    ),
    (
        "doctype with entities",
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;">]>'
        f"<svg {SVG}><text>&b;</text></svg>",
    ),
    (
        "external entity",
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        f"<svg {SVG}><text>&x;</text></svg>",
    ),
    (
        "plain doctype",
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        f"<svg {SVG}/>",
    ),
    (
        "xml-stylesheet instruction",
        f'<?xml-stylesheet href="https://x.test/a.css"?><svg {SVG}/>',
    ),
    ("animate to href", f'<svg {SVG}><a><set attributeName="href" to="javascript:x"/></a></svg>'),
    ("animate element", f'<svg {SVG}><animate attributeName="onload" values="alert(1)"/></svg>'),
    ("feImage", f'<svg {SVG}><filter id="f"><feImage href="https://x.test/a.png"/></filter></svg>'),
    ("unknown namespace attribute", f'<svg {SVG} xmlns:ev="urn:x"><rect ev:event="x"/></svg>'),
    ("not svg root", '<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>'),
    ("svg without namespace", "<svg><rect/></svg>"),
    ("not xml", "<svg"),
    ("empty", ""),
]


@pytest.mark.parametrize(("name", "document"), MALICIOUS, ids=[name for name, _ in MALICIOUS])
def test_a_malicious_svg_is_refused(name: str, document: str) -> None:
    with pytest.raises(UnsafeSvgError):
        sanitise_svg(document.encode())


def test_the_refusal_says_why() -> None:
    with pytest.raises(UnsafeSvgError, match="script"):
        sanitise_svg(f"<svg {SVG}><script>alert(1)</script></svg>".encode())
    with pytest.raises(UnsafeSvgError, match="onload"):
        sanitise_svg(f'<svg {SVG} onload="x"/>'.encode())


def test_an_oversized_svg_is_refused() -> None:
    padding = "<!--" + "x" * MAX_SVG_BYTES + "-->"
    with pytest.raises(UnsafeSvgError, match="large"):
        sanitise_svg(f"<svg {SVG}>{padding}</svg>".encode())


def test_a_safe_logo_is_accepted_and_re_serialised_without_comments() -> None:
    cleaned = sanitise_svg(SAFE_LOGO.encode())

    text = cleaned.decode()
    assert "<!--" not in text
    assert "exported by an editor" not in text
    root = fromstring(cleaned)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 160 80"
    assert text.count("<path") == 1
    assert "url(#g)" in text
    # The output is itself accepted: sanitising is idempotent.
    assert sanitise_svg(cleaned) == cleaned


def test_the_output_uses_the_default_svg_namespace() -> None:
    cleaned = sanitise_svg(f'<svg {SVG} viewBox="0 0 1 1"><rect width="1"/></svg>'.encode())

    assert cleaned.decode().startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "ns0:" not in cleaned.decode()


# --- Resource limits (lane-a security review of THM.12)


def _best_of(run: Callable[[], object], repeats: int = 9) -> float:
    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        run()
        best = min(best, time.perf_counter() - started)
    return best


def assert_scales_linearly(make_document: Callable[[int], str], n: int) -> None:
    """Time the strip on sizes n and 4n (best of 9 each, so load spikes drop out) and assert the
    ratio is at most 10. Linear work gives about 4 (up to about 8 under cache and allocator noise
    on a shared CI runner, seen once at 8.1 and once at 10.1), quadratic gives 16, so 10 keeps a
    clear gap on both sides; a pair over the limit is measured again, up to three times.
    MAX_SVG_BYTES caps 4n, so the n run cannot be made much longer than 20 ms; that is why the
    limit is not tighter. Unlike counting calls, this also catches quadratic work done inside the
    C regex or XML engines. Choose the largest n for which 4n still fits MAX_SVG_BYTES."""
    small, large = make_document(n).encode(), make_document(4 * n).encode()
    assert len(large) < MAX_SVG_BYTES

    def strip(document: bytes) -> Callable[[], object]:
        def run() -> None:
            with contextlib.suppress(UnsafeSvgError):  # a refusal is also a full pass
                sanitise_svg(document)

        return run

    # A shared CI runner can still land one slow sample on the small size (seen at 10.1x), so the
    # pair is measured up to three times: linear work passes on a later try, quadratic work (about
    # 16x) fails all three.
    ratios = []
    for _ in range(3):
        t_small, t_large = _best_of(strip(small)), _best_of(strip(large))
        ratios.append(t_large / t_small)
        if t_large <= 10 * t_small:
            return
    raise AssertionError(f"4n took {min(ratios):.1f}x the time of n at best (limit 10x)")


def test_many_comments_are_stripped_in_linear_time() -> None:
    assert_scales_linearly(lambda n: f"<svg {SVG}>" + "<!---->x<g/>" * n + "</svg>", 10000)
    cleaned = sanitise_svg((f"<svg {SVG}>" + "<!---->x<g/>" * 10 + "</svg>").encode()).decode()
    assert "<!--" not in cleaned


def test_text_around_stripped_comments_is_kept_in_order() -> None:
    document = f"<svg {SVG}><text>a<!-- 1 -->b<!-- 2 -->c<tspan>d</tspan>e<!-- 3 -->f</text></svg>"

    cleaned = sanitise_svg(document.encode()).decode()

    assert "<text>abc<tspan>d</tspan>ef</text>" in cleaned


def test_deep_nesting_is_refused_not_a_crash() -> None:
    document = f"<svg {SVG}>" + "<g>" * 1000 + "</g>" * 1000 + "</svg>"

    with pytest.raises(UnsafeSvgError, match="nested"):
        sanitise_svg(document.encode())


def test_moderate_nesting_is_accepted() -> None:
    document = f"<svg {SVG}>" + "<g>" * 30 + '<rect width="1"/>' + "</g>" * 30 + "</svg>"

    assert b"<rect" in sanitise_svg(document.encode())


def test_an_unknown_encoding_is_refused() -> None:
    document = f'<?xml version="1.0" encoding="x-foo"?><svg {SVG}/>'

    with pytest.raises(UnsafeSvgError):
        sanitise_svg(document.encode())


# --- Regex backtracking (lane-b second security review of THM.12)


def test_many_unclosed_urls_in_a_style_element_are_refused_in_linear_time() -> None:
    assert_scales_linearly(lambda n: f"<svg {SVG}><style>" + "url(" * n + "</style></svg>", 30000)


def test_many_unclosed_urls_in_an_attribute_are_refused_in_linear_time() -> None:
    assert_scales_linearly(lambda n: f'<svg {SVG}><rect fill="' + "url(" * n + '"/></svg>', 30000)


def test_many_unclosed_quoted_urls_are_refused_in_linear_time() -> None:
    assert_scales_linearly(lambda n: f"<svg {SVG}><style>" + "url('" * n + "</style></svg>", 24000)


def test_many_quotes_in_css_are_checked_in_linear_time() -> None:
    assert_scales_linearly(
        lambda n: f"<svg {SVG}><style>a{{b:" + "\"'" * n + "'}</style></svg>", 60000
    )


def test_too_many_url_references_in_one_value_are_refused() -> None:
    style = " ".join(f".c{n} {{ fill: url(#g); }}" for n in range(300))
    document = f'<svg {SVG}><defs><linearGradient id="g"/></defs><style>{style}</style></svg>'

    with pytest.raises(UnsafeSvgError, match="url"):
        sanitise_svg(document.encode())


@pytest.mark.parametrize(
    "value", ["url(#g)", "url( #g )", "url('#g')", 'url( "#g" )', "url(#a) url(#b)"]
)
def test_fragment_urls_are_still_accepted(value: str) -> None:
    document = f'<svg {SVG}><rect fill="{value.replace(chr(34), "&quot;")}"/></svg>'

    sanitise_svg(document.encode())


@pytest.mark.parametrize(
    "value",
    [
        "url(#g",
        "url('#g)",
        "url('#g\")",
        "url(url(#g))",
        "url(#g')",
        "url (#g)",
        "url(https://example.com/x)",
    ],
)
def test_malformed_or_external_urls_are_still_refused(value: str) -> None:
    document = f'<svg {SVG}><rect fill="{value.replace(chr(34), "&quot;")}"/></svg>'

    with pytest.raises(UnsafeSvgError):
        sanitise_svg(document.encode())


# Presentation attributes are parsed as CSS by browsers, so a CSS escape there is a `url(` the
# url() check cannot see (lane-b security re-check of #151).
@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("mask", r"u\72l(https://e.x/m.svg#m)"),
        ("fill", r"\75rl(//e.x/a#b)"),
        ("fill", "image-set('https://e.x' 1x)"),
        ("stroke", r"u\72l(https://e.x/s#s)"),
        ("filter", r"\000075rl(//e.x/f#f)"),
        ("clip-path", r"U\52L(https://e.x/c#c)"),
        ("marker-start", r"u\72l(https://e.x/m#m)"),
        ("cursor", r"u\72l(https://e.x/c.cur), auto"),
        ("fill", "IMAGE-SET('https://e.x' 1x)"),
        ("fill", "image-set ( 'https://e.x' 1x )"),
        ("stroke", "image('https://e.x/i.png')"),
        ("mask", "element(#x)"),
        ("fill", "src('https://e.x/a')"),
        ("fill", "cross-fade(url(#a), url(#b), 50%)"),
        ("fill", r"\ u\72l(//e.x/a#b)"),
        ("stroke", r"#000\9"),
    ],
)
def test_css_escapes_and_forbidden_functions_in_attributes_are_refused(
    attribute: str, value: str
) -> None:
    escaped = value.replace("&", "&amp;").replace(chr(34), "&quot;")
    document = f'<svg {SVG}><rect {attribute}="{escaped}"/></svg>'

    with pytest.raises(UnsafeSvgError):
        sanitise_svg(document.encode())


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("mask", "url(#m)"),
        ("clip-path", "url( '#c' )"),
        ("filter", "url(#f)"),
        ("stroke", "url(#g)"),
        ("aria-label", "Brand image (logo)"),
    ],
)
def test_fragment_references_and_plain_text_still_pass_in_attributes(
    attribute: str, value: str
) -> None:
    document = f'<svg {SVG}><rect {attribute}="{value}"/></svg>'

    sanitise_svg(document.encode())
