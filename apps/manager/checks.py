"""The Manager URL sweep and system checks (CORE.17, spec 02 §Backoffice "plus a sweep").

`manager_route_problems` walks a URLconf and reports every route registered under `/manager/`
that is not under exactly one gated space prefix, is a regex (whose reach cannot be read), or
whose view does not name a registered section of that space. The unit suite asserts the project
has none, and `manage.py check` fails on any (manager.E001), so a view cannot be added ungated.
Catch-all routes outside `/manager/` (the CMS pages) are the gate's business: it answers 404 to
every path under `/manager/` outside both spaces.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from django.conf import settings
from django.core import checks
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.resolvers import RegexPattern

from .navigation import registry, section_key
from .spaces import MANAGER_PREFIX, Space, space_of

GATE = "apps.manager.middleware.ManagerGateMiddleware"
_MANAGER_ROUTE = MANAGER_PREFIX.removeprefix("/")  # "manager/"
_REGEX_SYNTAX = re.compile(r"[.^$*+?{}\[\]\\|()]")

type UrlConf = str | Sequence[URLPattern | URLResolver] | None


def _text(part: Any) -> tuple[str, str, bool]:
    """One pattern as (text, literal start, is_regex)."""
    if isinstance(part, RegexPattern):
        text = str(part).removeprefix("^").removesuffix("$")
        cut = _REGEX_SYNTAX.search(text)
        return text, (text[: cut.start()] if cut else text), True
    text = str(part)
    return text, text.split("<", 1)[0], False


def _walk(
    patterns: Sequence[URLPattern | URLResolver], parts: tuple[Any, ...] = ()
) -> Iterator[tuple[tuple[Any, ...], Callable[..., Any]]]:
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, (*parts, pattern.pattern))
        else:
            yield (*parts, pattern.pattern), pattern.callback


def _patterns(urlconf: UrlConf) -> Sequence[URLPattern | URLResolver]:
    if urlconf is None or isinstance(urlconf, str):
        return get_resolver(urlconf).url_patterns
    return urlconf


def _manager_leaves(urlconf: UrlConf) -> Iterator[tuple[str, bool, Callable[..., Any]]]:
    """(route, is_regex, view) of every route whose literal start is under `/manager/`."""
    for parts, callback in _walk(_patterns(urlconf)):
        route, literal, is_regex, open_ended = "", "", False, False
        for part in parts:
            text, start, regex = _text(part)
            route += text
            is_regex = is_regex or regex
            if not open_ended:
                literal += start
                open_ended = start != text
        if literal.startswith(_MANAGER_ROUTE) or literal == _MANAGER_ROUTE.rstrip("/"):
            yield route, is_regex, callback


def manager_routes(urlconf: UrlConf = None) -> list[tuple[str, Space | None]]:
    """Every route under `/manager/` with its space (None outside both)."""
    return [(route, space_of("/" + route)) for route, _, _ in _manager_leaves(urlconf)]


def manager_route_problems(urlconf: UrlConf = None) -> list[str]:
    problems = []
    for route, is_regex, callback in _manager_leaves(urlconf):
        space = space_of("/" + route)
        if is_regex:
            problems.append(f"/{route}: a regex route under the Manager; use path() under a space")
            continue
        if space is None:
            problems.append(f"/{route}: not under exactly one gated prefix (shop/ or platform/)")
            continue
        key = section_key(callback)
        section = registry.get(key) if key else None
        if section is None:
            problems.append(f"/{route}: the view names no registered Manager section")
        elif section.space is not space:
            problems.append(
                f"/{route}: the view's section {section.key!r} belongs to the {section.space} "
                f"space, not {space}"
            )
    return problems


def check_manager_urls(app_configs: object = None, **kwargs: object) -> list[checks.CheckMessage]:
    return [
        checks.Error(problem, hint="Mount Manager views under a space prefix.", id="manager.E001")
        for problem in manager_route_problems()
    ]


def check_gate_installed(app_configs: object = None, **kwargs: object) -> list[checks.CheckMessage]:
    middleware: list[str] = list(settings.MIDDLEWARE)
    after = "apps.accounts.middleware.MfaRequiredMiddleware"
    if GATE not in middleware or (
        after in middleware and middleware.index(GATE) < middleware.index(after)
    ):
        return [
            checks.Error(
                f"{GATE} must be in MIDDLEWARE, after the authentication and MFA middlewares.",
                id="manager.E002",
            )
        ]
    return []
