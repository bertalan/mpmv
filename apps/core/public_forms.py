"""The sweep of public forms (SEC.13, spec 12): every route that takes a POST from somebody who is
not signed in must say how it is protected, or `manage.py check` fails (core.E701, core.E702).

How a route is found. `public_form_report` walks the URLconf recursively (every `include`, the
`i18n_patterns` language prefix as `<lang>/`) and looks at each leaf:

1. A leaf with a **declaration** is validated and listed. A declaration is one of the markers
   below set on the view (a function, or a class-based view's class), or `declare_public_form`
   for a view we do not own, keyed by its URL name.
2. A leaf under a **gated prefix** (`settings.PUBLIC_FORM_GATED_PREFIXES`: the Manager, the
   admins, the API, the style guide) is not listed: something in front of it refuses an
   anonymous request, and the reason is written next to the prefix. The anonymous exceptions
   under such a prefix (a login form) carry a declaration.
3. Any other leaf that **accepts a POST** fails the check. "Accepts a POST" is read from the view:
   a class-based view with a `post` handler, or a function whose method list (`require_POST`,
   `require_http_methods`) names POST or, with no such list, whose source mentions `POST`. When
   the view cannot be read it counts as accepting a POST (fail closed).

This is introspection of the view, not a request: the check needs no database and no client. Its
limit is the one of any static sweep: a view that reads `request.POST` through an alias the
source does not spell out is only found when its method list says POST, and a page served by a
catch-all (Wagtail pages) is not looked into. The declaration is a statement a reviewer reads;
the check verifies that it is complete (a captcha form renders the widget, a signed token or an
allow-list entry names a rate limit and a reason), not that the limit works. Those are tested
where the mechanism lives.

Three kinds of declaration:

- `public_form`: an anonymous POST. Protection is the captcha (`captcha_widget` in the named
  templates plus the server-side verify), a one-time signed token (CORE.15 action links), or an
  allow-list entry with a written reason. A signed token and an allow-list entry also need a
  rate limit; a request that does no work for a stranger (a redirect, a signature checked before
  anything else) may state `no_rate_limit` with the reason instead.
- `pre_mfa_step`: a signed-in user who has not yet verified a second factor (the MFA pages).
- `not_anonymous`: a POST that answers an anonymous request with a refusal, and why.

`known_gap` marks a form that is reachable and not protected yet. It is a warning (core.W701),
listed in the register, and names the task that closes it.
"""

import enum
import inspect
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.resolvers import LocalePrefixPattern, RegexPattern

SPEC_ATTR = "public_form_spec"
MFA_EXEMPT_ATTR = "mfa_exempt"  # set by apps.accounts.decorators.mfa_exempt
CAPTCHA_TAG = "captcha_widget"
_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"})
_POST_WORD = re.compile(r"\bPOST\b")

type UrlConf = str | Sequence[URLPattern | URLResolver] | None


class Kind(enum.StrEnum):
    PUBLIC = "public"
    PRE_MFA = "pre_mfa"
    NOT_ANONYMOUS = "not_anonymous"


class Protection(enum.StrEnum):
    CAPTCHA = "captcha"
    SIGNED_TOKEN = "signed token"  # noqa: S105 (a label, not a secret)
    ALLOW = "allow-list"


@dataclass(frozen=True)
class FormSpec:
    kind: Kind
    purpose: str
    task: str
    protection: Protection | None = None
    reason: str = ""
    rate_limit: str = ""
    no_rate_limit: str = ""
    templates: tuple[str, ...] = ()
    known_gap: str = ""


def public_form[T](
    *,
    protection: Protection | str,
    purpose: str,
    task: str,
    reason: str = "",
    rate_limit: str = "",
    no_rate_limit: str = "",
    templates: tuple[str, ...] = (),
    known_gap: str = "",
) -> Callable[[T], T]:
    """Declare an anonymous POST form on a view function or a class-based view's class."""
    spec = FormSpec(
        Kind.PUBLIC,
        purpose,
        task,
        Protection(protection),
        reason,
        rate_limit,
        no_rate_limit,
        templates,
        known_gap,
    )
    return _marker(spec)


def pre_mfa_step[T](*, purpose: str, task: str) -> Callable[[T], T]:
    """Declare a step a signed-in user reaches before the second factor is verified."""
    return _marker(FormSpec(Kind.PRE_MFA, purpose, task))


def not_anonymous[T](*, reason: str, purpose: str, task: str) -> Callable[[T], T]:
    """Declare a POST view that refuses an anonymous request (listed in no register)."""
    return _marker(FormSpec(Kind.NOT_ANONYMOUS, purpose, task, reason=reason))


def _marker[T](spec: FormSpec) -> Callable[[T], T]:
    def mark(view: T) -> T:
        setattr(view, SPEC_ATTR, spec)
        return view

    return mark


_declared: dict[str, FormSpec] = {}


def declare_public_form(url_name: str, spec: FormSpec) -> None:
    """Declare a view we do not own (a Wagtail or Django view), by its URL name (with namespace)."""
    _declared[url_name] = spec


@dataclass(frozen=True)
class Leaf:
    path: str  # "/vendor/apply/", language prefix shown as "<lang>/"
    name: str  # "namespace:name" or ""
    callback: Callable[..., Any]


@dataclass(frozen=True)
class Row:
    leaf: Leaf
    spec: FormSpec


@dataclass(frozen=True)
class Report:
    public: list[Row]
    pre_mfa: list[Row]
    not_anonymous: list[Row]
    errors: list[tuple[str, str]]  # (check id, message)
    warnings: list[tuple[str, str]]


def _pattern_text(pattern: Any) -> str:
    if isinstance(pattern, LocalePrefixPattern):
        return "[<lang>/]"  # the default language has no prefix
    if isinstance(pattern, RegexPattern):
        return str(pattern).removeprefix("^").removesuffix("$")
    return str(pattern)


def leaves(urlconf: UrlConf = None) -> Iterator[Leaf]:
    """Every route of the URLconf, recursively."""
    if urlconf is None or isinstance(urlconf, str):
        patterns: Sequence[URLPattern | URLResolver] = get_resolver(urlconf).url_patterns
    else:
        patterns = urlconf
    yield from _walk(patterns, "", "")


def _walk(
    patterns: Sequence[URLPattern | URLResolver], path: str, namespace: str
) -> Iterator[Leaf]:
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            ns = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from _walk(pattern.url_patterns, path + _pattern_text(pattern.pattern), ns)
        else:
            name = f"{namespace}{pattern.name}" if pattern.name else ""
            yield Leaf("/" + path + _pattern_text(pattern.pattern), name, pattern.callback)


def _unwrap(view: Callable[..., Any]) -> Iterator[Callable[..., Any]]:
    seen: set[int] = set()
    current: Any = view
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__wrapped__", None)


def spec_of(view: Callable[..., Any]) -> FormSpec | None:
    """The declaration on a view: its own, its class's, or one under a decorator."""
    for candidate in _unwrap(view):
        found = getattr(candidate, SPEC_ATTR, None)
        if found is None:
            found = getattr(getattr(candidate, "view_class", None), SPEC_ATTR, None)
        if isinstance(found, FormSpec):
            return found
    return None


def accepts_post(view: Callable[..., Any]) -> bool:
    """Whether the view takes a POST (see the module docstring); unreadable counts as yes."""
    view_class = getattr(view, "view_class", None)
    if view_class is not None:
        allowed = {m.upper() for m in getattr(view_class, "http_method_names", ())}
        return "POST" in allowed and hasattr(view_class, "post")
    chain = list(_unwrap(view))
    for fn in chain:  # require_POST / require_http_methods keep their list in a closure
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, list | tuple | set | frozenset) and value:
                methods = {str(m).upper() for m in value}
                if methods <= _HTTP_METHODS:
                    return "POST" in methods
    try:
        return any(_POST_WORD.search(inspect.getsource(fn)) for fn in chain)
    except (OSError, TypeError):
        return True


def _gate_for(path: str) -> str | None:
    for prefix in settings.PUBLIC_FORM_GATED_PREFIXES:
        if path.removeprefix("/").startswith(prefix):
            return str(prefix)
    return None


def _template_problem(name: str) -> str | None:
    try:
        source = get_template(name).template.source  # type: ignore[attr-defined]
    except TemplateDoesNotExist:
        return f"template {name!r} does not exist"
    except Exception as exc:
        return f"template {name!r} cannot be read ({type(exc).__name__})"
    return None if CAPTCHA_TAG in source else f"template {name!r} does not render {CAPTCHA_TAG}"


def _spec_problems(leaf: Leaf, spec: FormSpec) -> list[str]:
    if spec.kind is not Kind.PUBLIC:
        if spec.kind is Kind.NOT_ANONYMOUS and getattr(leaf.callback, MFA_EXEMPT_ATTR, False):
            return ["it is mfa_exempt, so it is reachable before the second factor"]
        return [] if spec.reason or spec.kind is Kind.PRE_MFA else ["it needs a written reason"]
    if spec.known_gap:
        return []  # reported as a warning, and listed in the register
    problems = []
    if spec.protection is Protection.CAPTCHA:
        if not spec.templates:
            problems.append("a captcha form must name the templates that render the widget")
        problems += [p for t in spec.templates if (p := _template_problem(t))]
    else:
        if not spec.reason:
            problems.append(f"{spec.protection} needs a written reason")
        if not (spec.rate_limit or spec.no_rate_limit):
            problems.append(
                f"{spec.protection} needs a rate limit (or no_rate_limit with a reason)"
            )
    return problems


def public_form_report(urlconf: UrlConf = None) -> Report:
    rows: dict[Kind, list[Row]] = {kind: [] for kind in Kind}
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    leaf_names = set()
    for leaf in leaves(urlconf):
        leaf_names.add(leaf.name)
        spec = spec_of(leaf.callback) or _declared.get(leaf.name)
        if spec is None:
            if _gate_for(leaf.path) is None and accepts_post(leaf.callback):
                errors.append(
                    (
                        "core.E701",
                        f"{leaf.path} ({leaf.name or 'unnamed'}) accepts a POST with no public "
                        "form declaration; mark it with public_form, pre_mfa_step or "
                        "not_anonymous (apps/core/public_forms.py)",
                    )
                )
            continue
        for problem in _spec_problems(leaf, spec):
            errors.append(("core.E702", f"{leaf.path} ({leaf.name or 'unnamed'}): {problem}"))
        if spec.known_gap:
            warnings.append(("core.W701", f"{leaf.path}: unprotected for now, {spec.known_gap}"))
        rows[spec.kind].append(Row(leaf, spec))
    for url_name in sorted(set(_declared) - leaf_names):
        if urlconf is None:  # a declaration for a route that does not exist is a typo
            errors.append(("core.E703", f"declared public form {url_name!r} matches no URL name"))
    for kind_rows in rows.values():
        kind_rows.sort(key=lambda row: row.leaf.path)
    return Report(rows[Kind.PUBLIC], rows[Kind.PRE_MFA], rows[Kind.NOT_ANONYMOUS], errors, warnings)


# --- The register (docs/dev/public-forms.md), built by tools/build_public_forms.py -------------

REGISTER_PATH = "docs/dev/public-forms.md"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip() or "-"


def _view_name(callback: Callable[..., Any]) -> str:
    target: Any = getattr(callback, "view_class", None) or list(_unwrap(callback))[-1]
    owner = getattr(target, "__qualname__", getattr(target, "__name__", repr(target)))
    return f"{getattr(target, '__module__', '?')}.{owner}"


def _protection_text(spec: FormSpec) -> str:
    text = spec.protection.value if spec.protection else "-"
    if spec.protection is Protection.CAPTCHA:
        return text
    return f"{text}: {spec.reason}" if spec.reason else text


def render_register(report: Report) -> str:
    lines = [
        "# Public forms",
        "",
        "> Generated by `tools/build_public_forms.py` (`make docs`) from the form declarations in",
        "> the code (SEC.13). **Do not edit by hand**: declare the form on its view with",
        "> `public_form` / `pre_mfa_step` (`apps/core/public_forms.py`) and run `make docs`.",
        "> `make check` fails when this file is stale, and `manage.py check` fails on an anonymous",
        "> POST route with no declaration (core.E701).",
        "",
        "## Anonymous POST routes",
        "",
        "A route that takes a POST from somebody who is not signed in. Protection is the captcha,",
        "a one-time signed token (CORE.15), or an allow-list entry with its reason.",
        "",
        "| URL name | Path | View | Purpose | Protection | Rate limit | Task |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.public:
        spec = row.spec
        rate = spec.rate_limit or (f"none: {spec.no_rate_limit}" if spec.no_rate_limit else "")
        if spec.known_gap:
            rate = f"NOT PROTECTED YET: {spec.known_gap}"
        lines.append(
            "| "
            + " | ".join(
                _cell(c)
                for c in (
                    f"`{row.leaf.name}`" if row.leaf.name else "",
                    f"`{row.leaf.path}`",
                    f"`{_view_name(row.leaf.callback)}`",
                    spec.purpose,
                    _protection_text(spec),
                    rate,
                    spec.task,
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Signed in, second factor not verified yet",
        "",
        "Steps a user reaches after the password and before the second factor (`mfa_exempt`). The",
        "user is authenticated, so no captcha applies; the MFA gate refuses everything else.",
        "",
        "| URL name | Path | View | Purpose | Task |",
        "|---|---|---|---|---|",
    ]
    for row in report.pre_mfa:
        lines.append(
            "| "
            + " | ".join(
                _cell(c)
                for c in (
                    f"`{row.leaf.name}`" if row.leaf.name else "",
                    f"`{row.leaf.path}`",
                    f"`{_view_name(row.leaf.callback)}`",
                    row.spec.purpose,
                    row.spec.task,
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Gated prefixes",
        "",
        "Routes under these prefixes are not swept one by one: something in front of them refuses",
        "an anonymous request. An anonymous exception under one (a login form) is declared above.",
        "",
        "| Prefix | Why it is gated |",
        "|---|---|",
    ]
    for prefix, why in sorted(settings.PUBLIC_FORM_GATED_PREFIXES.items()):
        lines.append(f"| `/{prefix}` | {_cell(why)} |")
    lines.append("")
    return "\n".join(lines)
