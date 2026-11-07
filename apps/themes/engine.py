"""Pure theme functions (spec 14 §14.2, §14.3): no ORM, no I/O.

A theme is a directory `themes/<slug>/` with a `theme.toml` manifest. Every theme descends from
`base`; its *chain* is the theme followed by each parent up to `base` (`["ride", "base"]`).
Templates are looked up along the chain, then in the app template directories.

`services.py` reads the manifests from disk and hands them to `build_registry`; the template
loader asks `template_candidates` where to look. THM.2 adds the token functions
(`resolve_tokens`, `contrast_ratio`, `check_tokens`, `render_tokens_css`) here.
"""

import posixpath
import re
from collections.abc import Container, Mapping
from dataclasses import dataclass, field

BASE_THEME = "base"
DIRECTIONS = ("ltr",)  # right-to-left languages are not planned (spec 09)
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MANIFEST_FILE = "theme.toml"
MISSING_BASE = f"themes/{BASE_THEME}: the {BASE_THEME!r} theme is missing or invalid"

# The Wagtail admin and the Django admin are never themed (spec 14 §14.3): a theme cannot
# override a template under these prefixes, whatever it ships.
UNTHEMED_PREFIXES = (
    "admin/",
    "registration/",
    "wagtailadmin/",
    "wagtaildocs/",
    "wagtailimages/",
    "wagtaillocales/",
    "wagtailsearch/",
    "wagtailsettings/",
    "wagtailsites/",
    "wagtailsnippets/",
    "wagtailusers/",
)

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
    if any(part in ("", ".", "..") for part in name.split("/")):
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
