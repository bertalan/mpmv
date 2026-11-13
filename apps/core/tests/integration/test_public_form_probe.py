"""SEC.13 review fixes: the sweep is tested, not only read. Every URL leaf that is not a declared
public form must refuse an anonymous POST, PUT, PATCH and DELETE (a login redirect, 401, 403, 404
or 405), gated prefixes included; so must the `not_anonymous` and pre-MFA routes. A leaf the
probe cannot build a path for (a regex it cannot fill in) is skipped and counted."""

import re

import pytest
from django.http import HttpResponse
from django.test import Client
from django.urls import Resolver404, resolve

from apps.core import public_forms
from apps.core.public_forms import Kind, Leaf, leaves, public_form_report, spec_of

pytestmark = pytest.mark.django_db

METHODS = ("post", "put", "patch", "delete")
REFUSED = {401, 403, 404, 405}
CONVERTER = re.compile(r"<(?:(\w+):)?(\w+)>")
REGEX_GROUP = re.compile(r"\(\?P<\w+>([^|)]*)[^)]*\)")


# Routes that answer an anonymous write with a normal page because they only read (the method is
# not looked at), each with why that is harmless. Anything else must refuse.
READ_ONLY = {
    "healthz": "liveness probe, no state",
    "wagtailadmin_sprite": "static icon sprite",
    "api-v1:openapi-json": "public API description",
    "api-v1:openapi-view": "public API documentation page",
    "home": "a Wagtail page; page types that read a POST are found by core.E704",
    "admin:logout": "ends the caller's own session, none for an anonymous caller",
}


def sample_path(route: str) -> str:
    """A concrete path for a route, or '' when none can be built."""
    path = route.replace("[<lang>/]", "")

    def fill(match: re.Match[str]) -> str:
        if match.group(1) == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        return "1" if match.group(1) in (None, "int") else "x"

    path = REGEX_GROUP.sub(lambda m: m.group(1) or "x", path)
    path = CONVERTER.sub(fill, path)
    path = path.replace(".*", "x")
    if re.search(r"[()\\^$*+?\[\]{}|]", path):
        return ""
    return path


def is_refusal(response: HttpResponse) -> bool:
    if response.status_code in REFUSED:
        return True
    if response.status_code in (301, 302, 303, 307, 308):
        return "login" in response["Location"] or "/account/" in response["Location"]
    return False


def probe_targets() -> list[tuple[Leaf, str]]:
    found = []
    for leaf in leaves():
        path = sample_path(leaf.path)
        if not path:
            continue
        try:
            match = resolve(path)
        except Resolver404:
            continue
        if match.func is leaf.callback:
            found.append((leaf, path))
    return found


def _declared_public(leaf: Leaf) -> bool:
    spec = spec_of(leaf.callback) or public_forms.declared_spec(leaf)
    return spec is not None and spec.kind is Kind.PUBLIC


def test_the_probe_reaches_most_of_the_routes() -> None:
    total = len(list(leaves()))
    assert len(probe_targets()) > total * 0.8, "the sample paths no longer resolve"


def test_every_undeclared_route_refuses_an_anonymous_write(client: Client) -> None:
    client.handler.enforce_csrf_checks = False
    failures = []
    for leaf, path in probe_targets():
        if _declared_public(leaf) or leaf.name in READ_ONLY:
            continue
        for method in METHODS:
            response: HttpResponse = getattr(client, method)(path)
            if not is_refusal(response):
                failures.append(
                    f"{method.upper()} {leaf.path} ({leaf.name}) -> {response.status_code}"
                )
    assert not failures, "anonymous writes were not refused:\n" + "\n".join(failures)


def test_not_anonymous_and_pre_mfa_routes_are_in_the_probe() -> None:
    report = public_form_report()
    probed = {leaf.path for leaf, _ in probe_targets()}
    for row in (*report.not_anonymous, *report.pre_mfa):
        assert row.leaf.path in probed, f"{row.leaf.path} cannot be probed"
