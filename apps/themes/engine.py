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

import posixpath
import re
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass, field

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
_OPTIONAL = ("parent", "description", "direction")


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


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ThemeError(f"{key!r} must be a non-empty string")
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
    return ThemeManifest(
        slug=slug,
        name=name,
        version=version,
        parent=parent,
        description=description,
        direction=str(direction),
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

Tokens = Mapping[str, str]

_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REFERENCE_RE = re.compile(r"\{([^{}]*)\}")
_TOKEN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
# A token value is pasted into a CSS declaration: nothing that could end it, open a block,
# escape a character, start a comment or close the stylesheet.
_FORBIDDEN_VALUE_RE = re.compile(r"[;{}<>\\\x00-\x1f\x7f]|/\*|\*/")
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


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
    if not isinstance(colour, str) or not _HEX_RE.match(colour):
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
_AROUND_RE = re.compile(r"\s*([{};,>])\s*")
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
    chain: Sequence[str], *, tokens_css: str, sources: Mapping[str, Sequence[str]]
) -> str:
    """One minified `theme.css`: the layer order, the tokens, then the CSS sources of the chain.

    Sources are concatenated from `base` down to the theme itself, each theme's files in the
    given order; the layer statement comes first, so the cascade follows the layers, not the
    file order.
    """
    parts = [f"@layer {', '.join(CSS_LAYERS)};", tokens_css]
    for slug in reversed(chain):
        parts.extend(sources.get(slug, ()))
    return minify_css("\n".join(parts)) + "\n"


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
