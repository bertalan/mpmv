"""Pure theme functions (spec 14 §14.2, §14.3): no ORM, no I/O.

A theme is a directory `themes/<slug>/` with a `theme.toml` manifest. Every theme descends from
`base`; its *chain* is the theme followed by each parent up to `base` (`["ride", "base"]`).
Templates are looked up along the chain, then in the app template directories.

`services.py` reads the manifests from disk and hands them to `build_registry`; the template
loader asks `template_candidates` where to look.

Design tokens (THM.2, §14.4): `parse_tokens` flattens a decoded `tokens.toml`, `resolve_tokens`
merges a chain (child values win) and resolves `{token}` references, `check_tokens` applies the
token contract (`contract.py`) with `contrast_ratio`, and `render_tokens_css` /
`build_theme_css` write the CSS that `build.py` saves.
"""

import hashlib
import math
import posixpath
import re
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import unquote

BASE_THEME = "base"
DIRECTIONS = ("ltr",)  # right-to-left languages are not planned (spec 09)
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MANIFEST_FILE = "theme.toml"
MISSING_BASE = f"themes/{BASE_THEME}: the {BASE_THEME!r} theme is missing or invalid"

# The Wagtail admin and the Django admin are never themed (spec 14 §14.3): a theme cannot
# override a template under these prefixes, nor any template in a directory whose name starts
# with "admin" (`accounts/admin/login.html`, the admin's login_template; `otp/admin111/`;
# `procrastinate/admin/`), whatever it ships.
UNTHEMED_PREFIXES = (
    "documents/",  # PDF documents (ORD.9): rendered by WeasyPrint, never served as HTML; own CSS
    "admin/",
    "registration/",
    "otp/",  # django-otp: admin login and device admin only
    "otp_hotp/",
    "otp_static/",
    "otp_totp/",
    "procrastinate/",  # job admin
    "wagtailadmin/",
    "wagtailcore/shared/",  # StreamField block previews, rendered inside the editor
    "wagtaildocs/",
    "wagtailimages/",
    "wagtaillocales/",
    "wagtailsearch/",
    "wagtailsettings/",
    "wagtailsites/",
    "wagtailsnippets/",
    "wagtailusers/",
)
UNTHEMED_DIRECTORY_PREFIX = "admin"

_REQUIRED = ("slug", "name", "version")
_OPTIONAL = (
    "parent",
    "description",
    "direction",
    "foundation",
    "css_budget_kb",
    "js_budget_kb",
)
FOUNDATIONS = ("bootstrap-5.3",)  # CSS frameworks a theme may declare (spec 14 §14.5)


class ThemeError(ValueError):
    """An invalid manifest, an unknown theme or a broken chain."""


@dataclass(frozen=True)
class ThemeManifest:
    """The parsed `theme.toml` of one theme."""

    slug: str
    name: str
    version: str
    parent: str | None  # None only for `base`
    description: str = ""
    direction: str = "ltr"
    foundation: str | None = None  # a name of FOUNDATIONS: the vendored framework under it
    css_budget_kb: int | None = None  # compressed theme.css limit; None keeps the default (§14.7)
    js_budget_kb: int | None = None  # compressed JavaScript limit of a page; None keeps the default


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ThemeError(f"{key!r} must be a non-empty string")
    return value


def _budget(data: Mapping[str, object], key: str) -> int | None:
    if key not in data:
        return None
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ThemeError(f"{key} must be a whole number of kilobytes, 1 or more")
    return value


def parse_manifest(data: Mapping[str, object], *, dirname: str) -> ThemeManifest:
    """Validate the decoded `theme.toml` of the theme stored in directory `dirname`."""
    unknown = sorted(set(data) - {*_REQUIRED, *_OPTIONAL})
    if unknown:
        raise ThemeError(f"unknown manifest keys: {', '.join(unknown)}")
    slug = _text(data, "slug")
    if not SLUG_RE.match(slug):
        raise ThemeError(f"slug {slug!r} must match {SLUG_RE.pattern}")
    if slug != dirname:
        raise ThemeError(f"slug {slug!r} differs from its directory {dirname!r}")
    name = _text(data, "name")
    version = _text(data, "version")
    parent = _text(data, "parent") if "parent" in data else None
    if slug == BASE_THEME and parent is not None:
        raise ThemeError("the base theme has no parent")
    if slug != BASE_THEME and parent is None:
        raise ThemeError(f"parent is required (every theme descends from {BASE_THEME!r})")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ThemeError("'description' must be a string")
    direction = data.get("direction", "ltr")
    if direction not in DIRECTIONS:
        raise ThemeError(f"direction {direction!r} is not supported ({', '.join(DIRECTIONS)})")
    foundation = None
    if "foundation" in data:
        foundation = data["foundation"]
        if foundation not in FOUNDATIONS:
            raise ThemeError(f"foundation {foundation!r} must be one of {', '.join(FOUNDATIONS)}")
    budgets = {key: _budget(data, key) for key in ("css_budget_kb", "js_budget_kb")}
    return ThemeManifest(
        slug=slug,
        name=name,
        version=version,
        parent=parent,
        description=description,
        direction=str(direction),
        foundation=str(foundation) if foundation is not None else None,
        css_budget_kb=budgets["css_budget_kb"],
        js_budget_kb=budgets["js_budget_kb"],
    )


def theme_chain(slug: str, registry: Mapping[str, ThemeManifest]) -> list[str]:
    """The theme and its parents up to `base`: `["ride", "base"]`.

    Raises ThemeError for an unknown theme or parent, a cycle, or a chain not ending in `base`.
    """
    chain: list[str] = []
    current: str | None = slug
    while current is not None:
        if current in chain:
            raise ThemeError(f"cycle in the parents of {slug!r}: {' -> '.join([*chain, current])}")
        manifest = registry.get(current)
        if manifest is None:
            raise ThemeError(f"unknown theme {current!r}")
        chain.append(current)
        current = manifest.parent
    if chain[-1] != BASE_THEME:
        raise ThemeError(f"the chain of {slug!r} ends in {chain[-1]!r}, not {BASE_THEME!r}")
    return chain


@dataclass(frozen=True)
class ThemeRegistry:
    """The valid themes (each with a chain ending in `base`) and why the others were left out."""

    manifests: Mapping[str, ThemeManifest] = field(default_factory=dict)
    chains: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def __contains__(self, slug: object) -> bool:
        return slug in self.manifests

    def __getitem__(self, slug: str) -> ThemeManifest:
        return self.manifests[slug]

    def foundation(self, slug: str) -> tuple[str, str] | None:
        """(theme that declares it, foundation name) for the nearest foundation in the chain."""
        for member in self.chain(slug):
            name = self.manifests[member].foundation
            if name is not None:
                return member, name
        return None

    def chain(self, slug: str) -> tuple[str, ...]:
        """The chain of a registered theme; LookupError for any other slug."""
        try:
            return self.chains[slug]
        except KeyError:
            raise LookupError(f"unknown theme {slug!r}") from None

    def choices(self) -> list[tuple[str, str]]:
        """(slug, name) pairs for a form: `base` first, then the others by slug."""
        order = sorted(self.manifests, key=lambda s: (s != BASE_THEME, s))
        return [(s, self.manifests[s].name) for s in order]


def build_registry(sources: Mapping[str, Mapping[str, object] | ThemeError]) -> ThemeRegistry:
    """Parse every theme directory (`dirname` -> decoded manifest, or the error reading it).

    Themes whose manifest is invalid or whose chain is broken are left out and reported.
    """
    errors: list[str] = []
    parsed: dict[str, ThemeManifest] = {}
    for dirname in sorted(sources):
        source = sources[dirname]
        try:
            if isinstance(source, ThemeError):
                raise source
            parsed[dirname] = parse_manifest(source, dirname=dirname)
        except ThemeError as exc:
            errors.append(f"themes/{dirname}: {exc}")
    if BASE_THEME not in parsed:
        errors.append(MISSING_BASE)
        return ThemeRegistry(errors=tuple(errors))
    chains: dict[str, tuple[str, ...]] = {}
    for slug in parsed:
        try:
            chains[slug] = tuple(theme_chain(slug, parsed))
        except ThemeError as exc:
            errors.append(f"themes/{slug}: {exc}")
    manifests = {slug: parsed[slug] for slug in parsed if slug in chains}
    return ThemeRegistry(manifests=manifests, chains=chains, errors=tuple(errors))


def is_themable(name: str) -> bool:
    """Whether a theme may provide template `name`: a safe relative path outside the admins."""
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if any(part.startswith(UNTHEMED_DIRECTORY_PREFIX) for part in parts[:-1]):
        return False
    return not name.startswith(UNTHEMED_PREFIXES)


def template_candidates(name: str, chain: list[str], *, root: str = "themes") -> list[str]:
    """Where to look for template `name`, child first: `<root>/<slug>/templates/<name>`.

    Empty for admin templates and unsafe names; the app template directories come after.
    """
    if not is_themable(name):
        return []
    return [posixpath.join(root, slug, "templates", name) for slug in chain]


def pick_theme(site_choice: str, env_default: str, registry: Container[str]) -> str:
    """The theme of a request: the Wagtail Site setting, then `MPMV_THEME_DEFAULT`, then `base`.

    A choice naming no registered theme (removed since it was saved) is skipped; the system
    checks report it (themes.E002, themes.E003).
    """
    for slug in (site_choice, env_default, BASE_THEME):
        if slug and slug in registry:
            return slug
    raise ThemeError(f"the {BASE_THEME!r} theme is not registered")


# --- Design tokens (THM.2, spec 14 §14.4, §14.5)

TOKENS_FILE = "tokens.toml"
CSS_LAYERS = ("reset", "tokens", "base", "layout", "components", "utilities", "overrides")
# A theme with a foundation (spec 14 §14.5): the framework's layer sits between reset and tokens.
FOUNDATION_CSS_LAYERS = (
    "reset",
    "bootstrap",
    "tokens",
    "base",
    "layout",
    "components",
    "utilities",
    "overrides",
)

Tokens = Mapping[str, str]

_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REFERENCE_RE = re.compile(r"\{([^{}]*)\}")
_TOKEN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
# A token value is pasted into a CSS declaration: nothing that could end it, open a block,
# escape a character, start a comment or close the stylesheet.
_FORBIDDEN_VALUE_RE = re.compile(r"[;{}<>\\\x00-\x1f\x7f]|/\*|\*/")
_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def _quotes_balanced(value: str) -> bool:
    """Every `'` or `"` string is closed (backslashes are rejected, so there are no escapes)."""
    quote: str | None = None
    for char in value:
        if quote is None:
            if char in "'\"":
                quote = char
        elif char == quote:
            quote = None
    return quote is None


def _check_css_value(name: str, value: str) -> None:
    """A value (references left out) that cannot leave its CSS declaration or string."""
    if _FORBIDDEN_VALUE_RE.search(value):
        raise ThemeError(f"token {name!r} has a character not allowed in a CSS value")
    if not _quotes_balanced(value):
        raise ThemeError(f"token {name!r} has an unbalanced quote")


def _check_value(name: str, value: str) -> None:
    if not value.strip():
        raise ThemeError(f"token {name!r} is empty")
    for reference in _REFERENCE_RE.findall(value):
        if not _TOKEN_NAME_RE.match(reference):
            raise ThemeError(f"token {name!r} refers to an invalid name {{{reference}}}")
    _check_css_value(name, _REFERENCE_RE.sub("", value))


def parse_tokens(data: Mapping[str, object]) -> dict[str, str]:
    """Flatten a decoded `tokens.toml`: `[color.red] 700 = "#B3261E"` -> `color.red.700`.

    Values are strings or numbers; a name has at least two lowercase segments. Raises
    ThemeError for anything else, and for values that could break out of a CSS declaration.
    """
    tokens: dict[str, str] = {}

    def walk(prefix: tuple[str, ...], table: Mapping[str, object]) -> None:
        for key, value in table.items():
            if not _SEGMENT_RE.match(key):
                raise ThemeError(f"invalid token name segment {key!r}")
            path = (*prefix, key)
            name = ".".join(path)
            if isinstance(value, Mapping):
                walk(path, value)
                continue
            if isinstance(value, bool) or not isinstance(value, str | int | float):
                raise ThemeError(f"token {name!r} must be a string or a number")
            if len(path) < 2:
                raise ThemeError(f"token {name!r} must sit in a table, like color.{name}")
            text = str(value)
            _check_value(name, text)
            tokens[name] = text

    walk((), data)
    return tokens


def resolve_tokens(chain: Sequence[str], tokens_by_theme: Mapping[str, Tokens]) -> dict[str, str]:
    """Merge the tokens of a chain (`["ride", "base"]`, child first) and resolve references.

    Child values override parent values; a theme missing from `tokens_by_theme` changes nothing.
    References (`"{color.red.700}"`, also inside a value) are resolved after the merge, so a
    child that changes a primitive changes every token derived from it. Unknown and cyclic
    references stay in the value, and `check_tokens` reports them.
    """
    merged: dict[str, str] = {}
    for slug in reversed(chain):
        merged.update(tokens_by_theme.get(slug, {}))

    resolved: dict[str, str] = {}
    resolving: set[str] = set()

    def resolve(name: str) -> str:
        if name in resolved:
            return resolved[name]
        resolving.add(name)

        def substitute(match: re.Match[str]) -> str:
            target = match.group(1)
            if target not in merged or target in resolving:
                return match.group(0)
            return resolve(target)

        value = _REFERENCE_RE.sub(substitute, merged[name])
        resolving.discard(name)
        resolved[name] = value
        return value

    for name in merged:
        resolve(name)
    return resolved


def _channels(colour: str) -> tuple[int, int, int]:
    if not isinstance(colour, str) or not _HEX_RE.fullmatch(colour):
        raise ThemeError(f"{colour!r} is not a #rgb or #rrggbb colour")
    digits = colour[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def relative_luminance(colour: str) -> float:
    """WCAG 2.x relative luminance of a `#rgb` or `#rrggbb` sRGB colour."""

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else float(((c + 0.055) / 1.055) ** 2.4)

    r, g, b = (linear(c) for c in _channels(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio, from 1 to 21 (unrounded: compare it with the minimum as is)."""
    light, dark = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@dataclass(frozen=True)
class ContrastPair:
    """Two colour tokens that must reach `minimum` contrast (4.5 for text, 3 for UI)."""

    foreground: str
    background: str
    minimum: float


@dataclass(frozen=True)
class TokenContract:
    """The tokens every theme must define and the contrast pairs they must satisfy."""

    required: tuple[str, ...]
    pairs: tuple[ContrastPair, ...]


@dataclass(frozen=True)
class TokenViolation:
    """One contract failure; `kind` is missing, unresolved, invalid or contrast."""

    kind: str
    token: str
    message: str

    def __str__(self) -> str:
        return f"{self.token}: {self.message}"


def _floor2(ratio: float) -> str:
    # Truncate, never round up: 4.497 is reported as 4.49, not as a passing-looking 4.50.
    return f"{int(ratio * 100) / 100:.2f}"


def check_tokens(tokens: Tokens, contract: TokenContract) -> list[TokenViolation]:
    """Missing required tokens, unresolved references, values that are not safe CSS once
    resolved (two harmless parts can join into `/*`), and contrast pairs below their minimum."""
    violations = [
        TokenViolation("missing", name, "required by the token contract")
        for name in contract.required
        if name not in tokens
    ]
    invalid: set[str] = set()
    for name, value in sorted(tokens.items()):
        if _REFERENCE_RE.search(value):
            violations.append(
                TokenViolation("unresolved", name, f"unresolved reference in {value!r}")
            )
            continue
        try:
            _check_css_value(name, value)
        except ThemeError as exc:
            invalid.add(name)
            violations.append(TokenViolation("invalid", name, f"resolved to {value!r}: {exc}"))
    for pair in contract.pairs:
        colours: list[str] = []
        for name in (pair.foreground, pair.background):
            colour = tokens.get(name)
            if colour is None or name in invalid or _REFERENCE_RE.search(colour):
                break  # reported above
            try:
                _channels(colour)
            except ThemeError as exc:
                invalid.add(name)
                violations.append(TokenViolation("invalid", name, str(exc)))
                break
            colours.append(colour)
        if len(colours) != 2:
            continue
        ratio = contrast_ratio(colours[0], colours[1])
        if ratio < pair.minimum:
            violations.append(
                TokenViolation(
                    "contrast",
                    f"{pair.foreground}/{pair.background}",
                    f"{colours[0]} on {colours[1]} is {_floor2(ratio)}:1, "
                    f"below the {pair.minimum:g}:1 minimum",
                )
            )
    return violations


def css_name(token: str) -> str:
    """The custom property of a token: `color.text-muted` -> `--color-text-muted`."""
    return "--" + token.replace(".", "-")


def render_tokens_css(tokens: Tokens) -> str:
    """`@layer tokens{:root{--color-text:...}}`, properties sorted by name."""
    body = ";".join(f"{css_name(name)}:{tokens[name]}" for name in sorted(tokens))
    return f"@layer tokens{{:root{{{body}}}}}\n"


# A CSS string ('…' or "…", escapes included) or a comment, whichever starts first.
_STRING_OR_COMMENT_RE = re.compile(
    r"""(?P<string>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(?P<comment>/\*.*?\*/)""",
    re.DOTALL,
)
_SPACE_RE = re.compile(r"\s+")
_AROUND_RE = re.compile(r"(?:(?<!\s)\s*)?([{};,>])\s*")  # starts at a run of spaces once
_AFTER_COLON_RE = re.compile(r":\s+")


def minify_css(css: str) -> str:
    """Drop comments and the whitespace CSS does not need.

    Whitespace stays where it can matter: before `:` (`.card :hover` is a descendant selector),
    around `+` and `-` (`calc()`), and between the words of a value. Strings (`content: " > "`)
    are copied as they are, and a `/*` inside a string is not a comment.
    """
    parts: list[str] = []
    outside: list[str] = []

    def flush() -> None:
        text = _SPACE_RE.sub(" ", "".join(outside))
        text = _AROUND_RE.sub(r"\1", text)
        text = _AFTER_COLON_RE.sub(":", text)
        parts.append(text.replace(";}", "}"))
        outside.clear()

    position = 0
    for match in _STRING_OR_COMMENT_RE.finditer(css):
        outside.append(css[position : match.start()])
        position = match.end()
        if match.group("string") is not None:
            flush()
            parts.append(match.group("string"))
    outside.append(css[position:])
    flush()
    return "".join(parts).strip()


def build_theme_css(
    chain: Sequence[str],
    *,
    tokens_css: str,
    sources: Mapping[str, Sequence[str]],
    foundation_css: str | None = None,
    bootstrap_map: str | None = None,
) -> str:
    """One minified `theme.css`: the layer order, the tokens, then the CSS sources of the chain.

    Sources are concatenated from `base` down to the theme itself, each theme's files in the
    given order; the layer statement comes first, so the cascade follows the layers, not the
    file order.

    With `foundation_css` (the framework's CSS, spec 14 §14.5) it is wrapped in `@layer
    bootstrap`, its `data:` URIs become same-origin SVG paths (`externalise_svgs`), and the
    `reset` layer of every source is dropped: the framework's own reboot replaces it.
    """
    if foundation_css is None:
        parts = [f"@layer {', '.join(CSS_LAYERS)};", tokens_css]
        for slug in reversed(chain):
            parts.extend(sources.get(slug, ()))
        return minify_css("\n".join(parts)) + "\n"
    external, _files = externalise_svgs(foundation_css.removeprefix('@charset "UTF-8";'))
    parts = [f"@layer {', '.join(FOUNDATION_CSS_LAYERS)};", f"@layer bootstrap{{{external}}}"]
    parts.append(tokens_css)
    if bootstrap_map is not None:
        parts.append(bootstrap_map)
    for slug in reversed(chain):
        parts.extend(strip_layer(source, "reset") for source in sources.get(slug, ()))
    return minify_css("\n".join(parts)) + "\n"


SVG_DIR = "svg"  # next to theme.css: externalised images are `svg/bs-<hash>.svg`
_DATA_SVG_RE = re.compile(
    r"""url\(\s*(?:"data:image/svg\+xml,([^"]*)"|'data:image/svg\+xml,([^']*)')\s*\)"""
)
_ANY_DATA_URI_RE = re.compile(r"""url\(\s*["']?data:""", re.IGNORECASE)


def externalise_svgs(css: str) -> tuple[str, dict[str, str]]:
    """Replace each `url("data:image/svg+xml,…")` by `url("svg/bs-<sha256[:12]>.svg")`.

    Returns the new CSS and the files (path relative to theme.css -> SVG text). The path comes
    from the image's own hash, so equal images share a file and the output is deterministic.
    Any other `data:` URI is an error: the CSP's `img-src` has no `data:` (spec 14 §14.5).
    """
    files: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        payload = match.group(1) if match.group(1) is not None else match.group(2)
        svg = unquote(payload)
        digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()[:12]
        path = f"{SVG_DIR}/bs-{digest}.svg"
        files[path] = svg
        return f'url("{path}")'

    result = _DATA_SVG_RE.sub(replace, css)
    if _ANY_DATA_URI_RE.search(result):
        raise ThemeError("the framework CSS holds a data: URI that is not an SVG image")
    return result, files


def strip_layer(css: str, layer: str) -> str:
    """`css` without its top-level `@layer <layer> { … }` blocks (braces and strings balanced)."""
    out: list[str] = []
    position = 0
    for match in re.finditer(rf"@layer\s+{re.escape(layer)}\s*\{{", css):
        if match.start() < position:
            continue
        depth, index = 1, match.end()
        while depth and index < len(css):
            if css[index] in "\"'":
                end = index + 1
                while end < len(css) and css[end] != css[index]:
                    end += 2 if css[end] == "\\" else 1
                index = end
            elif css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        out.append(css[position : match.start()])
        position = index
    out.append(css[position:])
    return "".join(out)


# --- Component helpers (THM.4, spec 14 §14.9)

HEADING_LEVELS = (2, 6)
_SAFE_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
# A browser drops tabs and newlines from a URL and reads "\" as "/", so "java\tscript:" or
# "/\evil.example" would turn into a script URL or a protocol-relative one.
_UNSAFE_HREF_CHARS_RE = re.compile(r"[\\\x00-\x1f\x7f]")


def safe_href(value: object) -> str:
    """`value` when a component may use it as a link target, else "" (render no link).

    Kept: http(s) URLs, and relative URLs starting with "/", "#" or "?". Dropped: every other
    scheme (javascript:, data:, vbscript:, mailto:, ...), protocol-relative "//host" URLs,
    bare relative paths, and values with a backslash or a control character.
    """
    if not isinstance(value, str):
        return ""
    href = value.strip()
    if not href or _UNSAFE_HREF_CHARS_RE.search(href):
        return ""
    if href.startswith("//"):
        return ""
    if href.startswith(("/", "#", "?")) or _SAFE_SCHEME_RE.match(href):
        return href
    return ""


def heading_level(value: object, default: int = 3) -> int:
    """A component heading level between 2 and 6.

    A missing or unreadable `value` gives `default`; an out-of-range one is clamped.
    """
    low, high = HEADING_LEVELS
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        level = value
    elif isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        level = int(value)
    else:
        return default
    return min(max(level, low), high)


# --- Palette derivation (THM.33, spec 14 §14.12)
#
# Pure colour maths for the palette builder. Contrast is always measured on the rounded
# `#RRGGBB` that would be stored, with `contrast_ratio` above (WCAG 2.2 relative luminance).

LIGHT_SURFACE = "#FFFFFF"
DARK_SURFACE = "#121417"  # the dark token set of spec 14 §14.4
TEXT_CONTRAST = 4.5  # WCAG 2.2 SC 1.4.3
UI_CONTRAST = 3.0  # WCAG 2.2 SC 1.4.11
FOREGROUNDS = ("#FFFFFF", "#1A1A1A")
LIGHTNESS_STEP = 0.001
HOVER_STEP = 0.07  # OKLCH lightness removed from a background for its hover state
TINT_LIGHTNESS = (0.96, 0.975, 0.99, 1.0)
TINT_CHROMA = 0.03
DEFAULT_FOCUS = "#2F80ED"
DEFAULT_CONTROL_BORDER = "#8A8A8A"

_OKLAB_EPSILON = 1e-6


def _to_linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else float(((c + 0.055) / 1.055) ** 2.4)


def _from_linear(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return 12.92 * value if value <= 0.0031308 else float(1.055 * value ** (1 / 2.4) - 0.055)


def _hex(colour: str) -> str:
    """`#RRGGBB`, upper case, from `#rgb` or `#rrggbb` (ThemeError for anything else)."""
    red, green, blue = _channels(colour)
    return f"#{red:02X}{green:02X}{blue:02X}"


def _to_oklch(colour: str) -> tuple[float, float, float]:
    """(L, C, hue in radians) of an sRGB colour (Ottosson's OKLab)."""
    r, g, b = (_to_linear(c) for c in _channels(colour))
    long = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    medium = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    short = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * long + 0.7936177850 * medium - 0.0040720468 * short
    a = 1.9779984951 * long - 2.4285922050 * medium + 0.4505937099 * short
    b2 = 0.0259040371 * long + 0.7827717662 * medium - 0.8086757660 * short
    return lightness, math.hypot(a, b2), math.atan2(b2, a)


def _oklch_linear(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    long = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    medium = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    short = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short,
        -1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short,
        -0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short,
    )


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-_OKLAB_EPSILON <= v <= 1 + _OKLAB_EPSILON for v in rgb)


def _from_oklch(lightness: float, chroma: float, hue: float) -> str:
    """`#RRGGBB` for an OKLCH colour; chroma is reduced only as far as needed to fit sRGB."""
    lightness = min(1.0, max(0.0, lightness))
    if not _in_gamut(_oklch_linear(lightness, chroma, hue)):
        low, high = 0.0, chroma
        for _ in range(24):
            middle = (low + high) / 2
            if _in_gamut(_oklch_linear(lightness, middle, hue)):
                low = middle
            else:
                high = middle
        chroma = low
    rgb = _oklch_linear(lightness, chroma, hue)
    red, green, blue = (round(_from_linear(v) * 255) for v in rgb)
    return f"#{red:02X}{green:02X}{blue:02X}"


def _lightness_scan(colour: str, against: str, min_ratio: float, *, darker: bool) -> str | None:
    lightness, chroma, hue = _to_oklch(colour)
    steps = round((lightness if darker else 1 - lightness) / LIGHTNESS_STEP)
    for step in range(1, steps + 1):
        shift = step * LIGHTNESS_STEP
        candidate = _from_oklch(lightness - shift if darker else lightness + shift, chroma, hue)
        if contrast_ratio(candidate, against) >= min_ratio:
            return candidate
    return None


def nearest_compliant(colour: str, against: str, min_ratio: float) -> str | None:
    """The closest colour to `colour` that reaches `min_ratio` against `against`.

    Keeps the OKLCH hue and chroma and moves only the lightness, in the direction that raises
    the contrast (darker when `against` is lighter than `colour`, lighter otherwise), by the
    smallest step (0.001 L) that passes; chroma shrinks only to stay inside sRGB. Returns
    `colour` itself when it already passes, and None when no lightness in that direction does.

    Contrast is WCAG 2.2 `(L1 + 0.05) / (L2 + 0.05)` with the relative luminance of
    https://www.w3.org/TR/WCAG22/#dfn-relative-luminance, see `contrast_ratio`.
    """
    if contrast_ratio(colour, against) >= min_ratio:
        return colour
    darker = relative_luminance(against) > relative_luminance(colour)
    return _lightness_scan(colour, against, min_ratio, darker=darker)


def suggest_foreground(background: str, min_ratio: float) -> str | None:
    """`#FFFFFF` or `#1A1A1A` for text on `background`, whichever has the higher contrast;
    None when neither reaches `min_ratio`."""
    best = max(FOREGROUNDS, key=lambda fg: contrast_ratio(fg, background))
    return best if contrast_ratio(best, background) >= min_ratio else None


def _all_compliant(colour: str, requirements: Sequence[tuple[str, float]]) -> str:
    """`colour` moved until it meets every (against, minimum), or unchanged when that is
    impossible (the palette check then reports the pair)."""
    current = colour
    for _ in range(4):
        failing = [(a, m) for a, m in requirements if contrast_ratio(current, a) < m]
        if not failing:
            return current
        moved = nearest_compliant(current, *failing[0])
        if moved is None:
            return colour
        current = moved
    return colour if any(contrast_ratio(current, a) < m for a, m in requirements) else current


def _hover(background: str, foreground: str) -> str:
    """A fixed OKLCH lightness step darker, kept at 4.5:1 with its foreground when it can."""
    lightness, chroma, hue = _to_oklch(background)
    hover = _from_oklch(lightness - HOVER_STEP, chroma, hue)
    if contrast_ratio(foreground, hover) >= TEXT_CONTRAST:
        return hover
    return nearest_compliant(hover, foreground, TEXT_CONTRAST) or background


def _tint(primary: str) -> str:
    """The lightest-needed tinted background on which the primary is still 4.5:1 text."""
    _, chroma, hue = _to_oklch(primary)
    tint = LIGHT_SURFACE
    for lightness in TINT_LIGHTNESS:
        tint = _from_oklch(lightness, min(chroma, TINT_CHROMA), hue)
        if contrast_ratio(primary, tint) >= TEXT_CONTRAST:
            break
    return tint


def derive_palette(
    base: Mapping[str, str],
    *,
    start: Mapping[str, str] | None = None,
    surfaces: Sequence[str] = (),
) -> dict[str, str]:
    """Every role of a palette from its base colours (spec 14 §14.12).

    `base` holds `primary` and, optionally, `secondary` (the accent) and `band`; an empty
    optional colour takes the value of `start`, a starting palette (ThemeError when there is
    none). The base colours are never changed, so a failing base shows up as a failing pair in
    `check_brand_palette`. `surfaces` are the extra surfaces of the registered themes (`#FFFFFF`
    is always included). Pure and deterministic.

    - `on_*` roles: `suggest_foreground` at 4.5:1 (white when neither candidate passes).
    - hover: a fixed OKLCH lightness step darker; tint: a light tint of the primary's hue.
    - `primary_dark`: `nearest_compliant` against `#121417` at 4.5:1.
    - `secondary_text`: `nearest_compliant` against every surface at 4.5:1.
    - `focus`, `control_border`: `nearest_compliant` at 3:1 against the surfaces (and the band
      for the focus ring).
    """
    chosen: dict[str, str] = {}
    for role in ("primary", "secondary", "band"):
        value = base.get(role) or (start or {}).get(role)
        if not value:
            raise ThemeError(f"no {role} colour and no starting palette")
        chosen[role] = _hex(value)
    primary, secondary, band = chosen["primary"], chosen["secondary"], chosen["band"]
    light = list(dict.fromkeys([LIGHT_SURFACE, *(_hex(s) for s in surfaces)]))

    on_primary = suggest_foreground(primary, TEXT_CONTRAST) or FOREGROUNDS[0]
    on_secondary = suggest_foreground(secondary, TEXT_CONTRAST) or FOREGROUNDS[0]
    text_on_band = suggest_foreground(band, TEXT_CONTRAST) or FOREGROUNDS[0]
    focus_start = _hex((start or {}).get("focus") or DEFAULT_FOCUS)
    border_start = _hex((start or {}).get("control_border") or DEFAULT_CONTROL_BORDER)
    return {
        "primary": primary,
        "primary_hover": _hover(primary, on_primary),
        "primary_tint": _tint(primary),
        "on_primary": on_primary,
        "primary_dark": nearest_compliant(primary, DARK_SURFACE, TEXT_CONTRAST) or primary,
        "secondary": secondary,
        "secondary_hover": _hover(secondary, on_secondary),
        "secondary_text": _all_compliant(secondary, [(s, TEXT_CONTRAST) for s in light]),
        "on_secondary": on_secondary,
        "band": band,
        "text_on_band": text_on_band,
        "focus": _all_compliant(focus_start, [(s, UI_CONTRAST) for s in [*light, band]]),
        "control_border": _all_compliant(border_start, [(s, UI_CONTRAST) for s in light]),
    }
