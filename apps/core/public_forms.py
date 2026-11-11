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

`known_gap` marks a form that is reachable and not protected yet. It is a message at INFO level
(core.I701: the deploy gate fails on warnings), listed in the register, and names the task that
closes it.
"""

import enum
import inspect
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
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
_POST_WORD = re.compile(
    r"\b(POST|PUT|PATCH|DELETE)\b|request\.(body|data)\b|\.method\s*(!=|not in|in\b)"
)
_TASK_ID = re.compile(r"[A-Z]{2,5}\.\d+")

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


_declared: dict[str, tuple[str, FormSpec]] = {}


def declare_public_form(url_name: str, spec: FormSpec, *, view: str) -> None:
    """Declare a view we do not own (a Wagtail or Django view): its URL name (with namespace) and
    the dotted path of the view, so a reused name does not inherit the declaration."""
    _declared[url_name] = (view, spec)


def declared_spec(leaf: "Leaf") -> FormSpec | None:
    found = _declared.get(leaf.name)
    return found[1] if found and found[0] == _view_name(leaf.callback) else None


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


WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _chain(view: Callable[..., Any]) -> list[Callable[..., Any]]:
    """The view, what it wraps (`__wrapped__`) and the functions in its closures, so a decorator
    written without `functools.wraps` does not hide the view it calls."""
    found: list[Callable[..., Any]] = []
    todo: list[Any] = [view]
    while todo and len(found) < 20:
        current = todo.pop(0)
        if current is None or any(current is seen for seen in found):
            continue
        found.append(current)
        todo.append(getattr(current, "__wrapped__", None))
        for cell in getattr(current, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if inspect.isfunction(value):
                todo.append(value)
    return found


def accepts_post(view: Callable[..., Any]) -> bool:
    """Whether the view takes a write (POST, PUT, PATCH, DELETE; see the module docstring);
    unreadable counts as yes."""
    view_class = getattr(view, "view_class", None)
    if view_class is not None:
        allowed = {m.upper() for m in getattr(view_class, "http_method_names", ())}
        return any(
            method in allowed and hasattr(view_class, method.lower()) for method in WRITE_METHODS
        )
    chain = _chain(view)
    for fn in chain:  # require_POST / require_http_methods keep their list in a closure
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, list | tuple | set | frozenset) and value:
                methods = {str(m).upper() for m in value}
                if methods <= _HTTP_METHODS:
                    return bool(methods & WRITE_METHODS)
    try:
        return any(_POST_WORD.search(inspect.getsource(fn)) for fn in chain)
    except (OSError, TypeError):
        return True


def _gate_for(path: str) -> str | None:
    for prefix in settings.PUBLIC_FORM_GATED_PREFIXES:
        if path.removeprefix("/").removeprefix("[<lang>/]").startswith(prefix):
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
    if spec.known_gap:  # reported as INFO (core.I701) and listed in the register
        return [] if _TASK_ID.fullmatch(spec.task) else ["a known gap needs a backlog task ID"]
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


def settings_problems() -> list[str]:
    """Wagtail features that add an anonymous form stay off unless the form is declared."""
    problems = []
    if settings.WAGTAIL_PASSWORD_RESET_ENABLED and "wagtailadmin_password_reset" not in _declared:
        problems.append(
            "WAGTAIL_PASSWORD_RESET_ENABLED adds an anonymous POST form: declare "
            "wagtailadmin_password_reset with declare_public_form"
        )
    options = getattr(settings, "WAGTAIL_PRIVATE_PAGE_OPTIONS", {})
    if (
        options.get("SHARED_PASSWORD", True)
        and "wagtailcore_authenticate_with_password" not in _declared
    ):
        problems.append(
            "page privacy by shared password adds an anonymous POST form: declare "
            "wagtailcore_authenticate_with_password"
        )
    return problems


def _page_classes() -> Iterator[type]:
    from wagtail.models import Page

    todo = list(Page.__subclasses__())
    seen: set[type] = set()
    while todo:
        cls = todo.pop()
        if cls not in seen:
            seen.add(cls)
            todo.extend(cls.__subclasses__())
            yield cls


def _reads_a_write(function: Any) -> bool:
    try:
        return bool(_POST_WORD.search(inspect.getsource(inspect.unwrap(function))))
    except (OSError, TypeError):
        return True


def page_problems(classes: Iterable[type] | None = None) -> list[str]:
    """Pages are served through one catch-all route, so the leaf walk cannot see their forms.
    A page type that overrides `serve` (or `serve_preview`), has a routable method, or is a
    Wagtail form page, and whose code reads a POST, must carry a `public_form` declaration."""
    from django.apps import apps

    form_page: type | None = None
    if apps.is_installed("wagtail.contrib.forms"):  # importing it otherwise adds a model
        from wagtail.contrib.forms.models import AbstractForm

        form_page = AbstractForm
    problems = []
    for cls in sorted(
        classes if classes is not None else _page_classes(),
        key=lambda c: f"{c.__module__}.{c.__qualname__}",
    ):
        declared = any(isinstance(getattr(base, SPEC_ATTR, None), FormSpec) for base in cls.__mro__)
        if declared or not cls.__module__.startswith("apps."):
            continue
        if form_page is not None and issubclass(cls, form_page):
            problems.append(
                f"{cls.__qualname__} is a Wagtail form page: declare it with public_form"
            )
            continue
        for base in cls.__mro__:
            if base.__module__.startswith("wagtail."):
                continue
            for name, attr in vars(base).items():
                routable = hasattr(attr, "_routablepage_routes")
                if (name in ("serve", "serve_preview") or routable) and _reads_a_write(attr):
                    problems.append(
                        f"{cls.__qualname__}.{name} reads a POST: declare the page type with "
                        "public_form"
                    )
    return problems


def public_form_report(urlconf: UrlConf = None) -> Report:
    rows: dict[Kind, list[Row]] = {kind: [] for kind in Kind}
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    leaf_names = set()
    for leaf in leaves(urlconf):
        leaf_names.add(leaf.name)
        spec = spec_of(leaf.callback) or declared_spec(leaf)
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
            warnings.append(("core.I701", f"{leaf.path}: unprotected for now, {spec.known_gap}"))
        rows[spec.kind].append(Row(leaf, spec))
    for url_name in sorted(set(_declared) - leaf_names):
        if urlconf is None:  # a declaration for a route that does not exist is a typo
            errors.append(("core.E703", f"declared public form {url_name!r} matches no URL name"))
    if urlconf is None:
        errors += [("core.E705", message) for message in settings_problems()]
        errors += [("core.E704", message) for message in page_problems()]
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
            rate = f"NOT PROTECTED ({spec.task}): {spec.known_gap}"
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
