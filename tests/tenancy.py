"""Cross-tenant test harness (CORE.3, spec 02 §2.2 "Tenant (vendor) isolation").

Given two vendors and an endpoint's declared access class, `check_endpoint` runs the negative
test the spec makes mandatory: Vendor A's member asks for Vendor B's data and must not get it.

- `VendorScoped`: the endpoint shows or changes one vendor-owned object. B's object must answer
  404 (not 403, not 200) to every declared method and stay unchanged; A's own object must not
  answer 404, which proves the URL and the object maker are right (positive control).
- `StaffOnly`: platform staff only (the admin). A vendor member gets no 2xx, the same status for
  B's object as for a missing one (no enumeration), and staff do reach the object.
- `VendorScopedList`: a collection of vendor-owned objects (a list, an export, a search page). With
  one object per vendor, B's identifier must not appear in the response (body or Location) to any
  declared method and B's object must stay unchanged; A's own identifier must appear in the
  response to the first method (positive control: the page lists what it should).
- `NoVendorData`: public pages, the user's own account, the API client itself. Vendor B first gets
  one object of every vendor-owned model (`CrossTenant.seed_b`), so a page that lists vendor data
  without a tenant filter has something to leak; the response to A's member must contain none of
  B's identifying values (`CrossTenant.markers`) nor the public ids of those objects.

`endpoints()` lists every URL pattern of a URLconf and `api_operations()` every API v1 operation,
so `tests.integration.test_tenant_isolation` generates one test per endpoint and fails when one
is not declared in `tests.tenancy_registry`.

Web endpoints are called by Vendor A's owner, signed in. API v1 operations go through
`check_api_operation` and are called with an API key bound to Vendor A that holds the
operation's scope (CORE.13): a vendor key asking for Vendor B's object gets 404, never 403.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote

from django.db import models
from django.http.response import HttpResponseBase
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, resolve
from django.utils.regex_helper import normalize
from ninja import NinjaAPI

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.api.tests.factories import ApiClientFactory
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.factory_registry import project_models

# --- Two tenants

# Creates an object owned by the given vendor.
ObjectMaker = Callable[[Vendor], models.Model]


@dataclass(frozen=True)
class Tenant:
    vendor: Vendor
    owner: User


@dataclass(frozen=True)
class CrossTenant:
    """`a` is the caller's vendor; `b` is the other vendor, whose data `a` must never reach.

    `makers` make one object of every vendor-owned model (`tests.tenancy_registry.OWNED_OBJECTS`
    for the `cross_tenant` fixture): `seed_b` gives one of each to Vendor B.
    """

    a: Tenant
    b: Tenant
    makers: tuple[ObjectMaker, ...] = ()

    def markers(self, *objects: models.Model) -> tuple[str, ...]:
        """Values that identify Vendor B, plus the public ids of B's `objects`.

        None may appear in a response to Vendor A.
        """
        vendor = self.b.vendor
        return (
            vendor.legal_name,
            str(vendor.public_id),
            vendor.contact_email,
            vendor.vat_number,
            self.b.owner.email,
            *(str(obj.public_id) for obj in objects if hasattr(obj, "public_id")),
        )

    def seed_b(self) -> tuple[models.Model, ...]:
        """One object of every vendor-owned model, owned by Vendor B."""
        return tuple(make(self.b.vendor) for make in self.makers)


def make_tenant(letter: str) -> Tenant:
    vendor = VendorFactory.create(
        display_name=f"Vendor {letter}", legal_name=f"Vendor {letter} S.r.l."
    )
    owner = UserFactory.create(email=f"owner-{letter.lower()}@example.com")
    VendorMemberFactory.create(vendor=vendor, user=owner, role=VendorMember.Role.OWNER)
    return Tenant(vendor=vendor, owner=owner)


def make_cross_tenant(makers: Iterable[ObjectMaker] = ()) -> CrossTenant:
    return CrossTenant(a=make_tenant("A"), b=make_tenant("B"), makers=tuple(makers))


# --- Access classes

# Signs the caller in on `client` and returns extra request headers (an API key, for example).
# An access class without one uses the default caller of its surface: `member_session` for web
# endpoints, `vendor_api_key(<the operation's scopes>)` for API v1 operations.
Login = Callable[[Client, CrossTenant], dict[str, str]]

# Given to a vendor key for an operation open to any valid key (`ApiKey(None)`).
ANY_KEY_SCOPE = "catalog:read"


def member_session(client: Client, tenants: CrossTenant) -> dict[str, str]:
    """Vendor A's owner, signed in with a verified second factor (owners need MFA, CORE.4)."""
    verified_login(client, tenants.a.owner)
    return {}


def vendor_api_key(scopes: Sequence[str]) -> Login:
    """An API key bound to Vendor A (CORE.13) with `scopes`, as an `Authorization` header."""

    def login(client: Client, tenants: CrossTenant) -> dict[str, str]:
        _, key = ApiClientFactory.create_with_key(
            vendor=tenants.a.vendor, scopes=list(scopes) or [ANY_KEY_SCOPE]
        )
        return {"Authorization": f"Bearer {key}"}

    return login


def by_public_id(obj: models.Model) -> dict[str, str]:
    return {"public_id": public_id_of(obj)}


def public_id_of(obj: models.Model) -> str:
    return str(obj.public_id)  # type: ignore[attr-defined]  # BaseModel


@dataclass(frozen=True)
class VendorScoped:
    """Shows or changes one vendor-owned object, found from the URL by `kwargs(obj)`."""

    make: ObjectMaker
    kwargs: Callable[[models.Model], dict[str, str]] = by_public_id
    methods: tuple[str, ...] = ("GET",)
    login: Login | None = None


@dataclass(frozen=True)
class VendorScopedList:
    """Lists, exports or searches vendor-owned objects; `identify(obj)` is how one shows up.

    `kwargs` fill the URL parameters, if any (a search term, a format).
    """

    make: ObjectMaker
    identify: Callable[[models.Model], str] = public_id_of
    methods: tuple[str, ...] = ("GET",)
    login: Login | None = None
    kwargs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StaffOnly:
    """Platform staff only. With `make`, the URL parameter `object_param` takes B's object."""

    reason: str
    make: ObjectMaker | None = None
    object_param: str = "object_id"
    kwargs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NoVendorData:
    """Shows no vendor-owned data: public, the caller's own account, the API client itself.

    `path` replaces the built URL when a pattern only catches what earlier patterns leave.
    """

    reason: str
    kwargs: Mapping[str, str] = field(default_factory=dict)
    login: Login | None = None
    path: str | None = None


type Access = VendorScoped | VendorScopedList | StaffOnly | NoVendorData


# --- Endpoints


@dataclass(frozen=True)
class Endpoint:
    """One URL pattern: `key` is the namespaced URL name, or the route when it has no name."""

    key: str
    route: str
    namespace: str
    params: tuple[str, ...]
    regex: str
    callback: Callable[..., Any]

    @property
    def is_api_operation(self) -> bool:
        """A django-ninja operation of API v1: covered per method through `api_operations()`.

        Wagtail's admin also serves django-ninja views; they are admin pages (`admin/`).
        """
        return getattr(self.callback, "__module__", "") == "ninja.operation" and (
            self.route.startswith("api/")
        )

    def path(self, **kwargs: str) -> str:
        missing = [name for name in self.params if name not in kwargs]
        if missing:
            raise KeyError(", ".join(missing))
        candidates = normalize(self.regex)
        fmt = next((f for f, names in candidates if set(names) == set(self.params)), None)
        if fmt is None:
            fmt = candidates[0][0]
        return "/" + fmt % {name: quote(str(kwargs[name]), safe="/") for name in self.params}


def _key(namespace: str, name: str | None, route: str) -> str:
    if not name:
        return route
    return f"{namespace}:{name}" if namespace else name


def _join_route(route: str, part: str) -> str:
    """As Django joins routes in `ResolverMatch.route`."""
    return part if not route else route + part.removeprefix("^")


def _walk(
    patterns: Iterable[URLPattern | URLResolver], regex: str, route: str, namespace: str
) -> Iterable[Endpoint]:
    for pattern in patterns:
        full_regex = regex + pattern.pattern.regex.pattern.removeprefix("^")
        full_route = _join_route(route, str(pattern.pattern))
        if isinstance(pattern, URLResolver):
            inner = ":".join(n for n in (namespace, pattern.namespace) if n)
            yield from _walk(pattern.url_patterns, full_regex, full_route, inner)
        else:
            yield Endpoint(
                key=_key(namespace, pattern.name, full_route),
                route=full_route,
                namespace=namespace,
                params=tuple(re.compile(full_regex).groupindex),
                regex=full_regex,
                callback=pattern.callback,
            )


def endpoints(urlconf: str | None = None) -> list[Endpoint]:
    """Every URL pattern of `urlconf` (default: the root URLconf), includes flattened."""
    return list(_walk(get_resolver(urlconf).url_patterns, "", "", ""))


def openapi_path(route: str) -> str:
    """`api/v1/products/<str:sku>` → `/api/v1/products/{sku}`."""
    return "/" + re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", route)


@dataclass(frozen=True)
class ApiOperation:
    method: str
    path: str  # as in the OpenAPI description
    endpoint: Endpoint
    scopes: tuple[str, ...] = ()  # API key scopes the operation requires (OpenAPI `security`)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _required_scopes(operation: Mapping[str, Any]) -> tuple[str, ...]:
    requirements = operation.get("security") or []
    return tuple(
        sorted({scope for req in requirements for scopes in req.values() for scope in scopes})
    )


def api_operations(urlconf: str | None = None, api: NinjaAPI | None = None) -> list[ApiOperation]:
    """Every operation of `api` (default: the published API v1), with the URL pattern serving it."""
    if api is None:
        from apps.api.v1.api import api as published

        api = published
    served = {openapi_path(e.route): e for e in endpoints(urlconf) if e.is_api_operation}
    operations: list[ApiOperation] = []
    for path, item in api.get_openapi_schema()["paths"].items():
        if path not in served:
            raise LookupError(f"API operation path {path} is not served by the URLconf")
        operations.extend(
            ApiOperation(method.upper(), path, served[path], _required_scopes(operation))
            for method, operation in item.items()
            if method in HTTP_METHODS
        )
    return operations


# --- Vendor-owned models


def vendor_owned_models(exclude: Mapping[str, str] | None = None) -> list[type[models.Model]]:
    """Vendor and every project model with a ForeignKey chain to it, minus `exclude` (labels)."""
    excluded = exclude or {}
    owned: set[type[models.Model]] = {Vendor}
    candidates = [m for m in project_models() if m._meta.label not in excluded]
    grew = True
    while grew:
        grew = False
        for model in candidates:
            if model in owned:
                continue
            if any(
                isinstance(f, models.ForeignKey) and f.related_model in owned
                for f in model._meta.get_fields()
            ):
                owned.add(model)
                grew = True
    return sorted(owned, key=lambda m: m._meta.label)


# --- Checks


def check_endpoint(
    client: Client, endpoint: Endpoint, access: Access, tenants: CrossTenant, *, method: str = "GET"
) -> None:
    """Run the negative cross-tenant test of `endpoint`; `method` is for `NoVendorData`."""
    match access:
        case VendorScoped():
            _check_vendor_scoped(client, endpoint, access, tenants)
        case VendorScopedList():
            _check_vendor_scoped_list(client, endpoint, access, tenants)
        case StaffOnly():
            _check_staff_only(client, endpoint, access, tenants)
        case NoVendorData():
            _check_no_vendor_data(client, endpoint, access, tenants, method)


def _request(
    client: Client, method: str, path: str, headers: Mapping[str, str]
) -> HttpResponseBase:
    response: HttpResponseBase = client.generic(method, path, headers=headers)
    return response


def _body(response: HttpResponseBase) -> str:
    content = (
        b"".join(response.streaming_content)  # type: ignore[attr-defined]
        if response.streaming
        else response.content  # type: ignore[attr-defined]
    )
    return bytes(content).decode(errors="replace") + response.headers.get("Location", "")


def _assert_routes_to(endpoint: Endpoint, path: str) -> None:
    match = resolve(path)
    found = _key(match.namespace, match.url_name, match.route)
    assert found == endpoint.key, f"{path} routes to {found}, not to {endpoint.key}"


def _assert_no_markers(
    endpoint: Endpoint,
    response: HttpResponseBase,
    tenants: CrossTenant,
    *objects: models.Model,
    body: str | None = None,
    known: str = "",
) -> None:
    """Assert that the response (or its `body`, already read) shows nothing of B or B's `objects`.

    A streaming response can be read only once, so a caller that needs the body passes it.
    Markers the caller sent in the request path (`known`) may be echoed back, e.g. as the
    `instance` of an API problem: they tell the caller nothing new.
    """
    body = _body(response) if body is None else body
    for marker in tenants.markers(*objects):
        if marker in known:
            continue
        assert marker not in body, (
            f"{endpoint.key}: response shows another vendor's data ({marker})"
        )


def _snapshot(obj: models.Model) -> dict[str, Any] | None:
    return type(obj)._default_manager.filter(pk=obj.pk).values().first()


def check_api_operation(
    client: Client, operation: ApiOperation, access: Access, tenants: CrossTenant
) -> None:
    """The negative cross-tenant test of an API v1 operation, called with Vendor A's API key.

    Unless the access class names its own `login`, the caller is an API key bound to Vendor A
    with the operation's scope (`vendor_api_key`), and the operation's method is the one tested.
    """
    match access:
        case VendorScoped() | VendorScopedList():
            login = access.login or vendor_api_key(operation.scopes)
            access = replace(access, login=login, methods=(operation.method,))
        case NoVendorData():
            access = replace(access, login=access.login or vendor_api_key(operation.scopes))
        case StaffOnly():
            raise TypeError(
                f"{operation.key}: StaffOnly describes staff web pages; an API operation is "
                "VendorScoped, VendorScopedList or NoVendorData"
            )
    check_endpoint(client, operation.endpoint, access, tenants, method=operation.method)


def _login(access: VendorScoped | VendorScopedList | NoVendorData) -> Login:
    return access.login or member_session


def _check_vendor_scoped(
    client: Client, endpoint: Endpoint, access: VendorScoped, tenants: CrossTenant
) -> None:
    headers = _login(access)(client, tenants)
    mine, theirs = access.make(tenants.a.vendor), access.make(tenants.b.vendor)
    their_path = endpoint.path(**access.kwargs(theirs))
    _assert_routes_to(endpoint, their_path)

    for method in access.methods:
        before = _snapshot(theirs)
        response = _request(client, method, their_path, headers)
        assert response.status_code == 404, (
            f"{endpoint.key}: {method} on another vendor's object: "
            f"expected 404, got {response.status_code}"
        )
        assert _snapshot(theirs) == before, (
            f"{endpoint.key}: {method} changed another vendor's object before answering 404"
        )
        _assert_no_markers(endpoint, response, tenants, known=their_path)

    own = _request(client, access.methods[0], endpoint.path(**access.kwargs(mine)), headers)
    assert own.status_code != 404, (
        f"{endpoint.key}: the caller's own object answers 404 too, so the URL kwargs or the "
        "object maker are wrong and the cross-tenant 404 proves nothing"
    )


def _sample_path(endpoint: Endpoint, kwargs: Mapping[str, str]) -> str:
    return endpoint.path(**{name: kwargs.get(name, "0") for name in endpoint.params})


def _check_vendor_scoped_list(
    client: Client, endpoint: Endpoint, access: VendorScopedList, tenants: CrossTenant
) -> None:
    headers = _login(access)(client, tenants)
    mine, theirs = access.make(tenants.a.vendor), access.make(tenants.b.vendor)
    path = _sample_path(endpoint, access.kwargs)
    _assert_routes_to(endpoint, path)

    bodies = []
    for method in access.methods:
        before = _snapshot(theirs)
        response = _request(client, method, path, headers)
        body = _body(response)
        assert access.identify(theirs) not in body, (
            f"{endpoint.key}: {method} lists another vendor's object ({access.identify(theirs)})"
        )
        _assert_no_markers(endpoint, response, tenants, theirs, body=body)
        assert _snapshot(theirs) == before, (
            f"{endpoint.key}: {method} changed another vendor's object"
        )
        bodies.append(body)

    assert access.identify(mine) in bodies[0], (
        f"{endpoint.key}: the caller's own object ({access.identify(mine)}) is not listed "
        "either, so the URL, `identify` or the object maker are wrong and the absence of "
        "another vendor's object proves nothing"
    )


def _check_staff_only(
    client: Client, endpoint: Endpoint, access: StaffOnly, tenants: CrossTenant
) -> None:
    member_session(client, tenants)
    kwargs = {name: access.kwargs.get(name, "0") for name in endpoint.params}
    theirs = None
    if access.make is not None and access.object_param in endpoint.params:
        theirs = access.make(tenants.b.vendor)
        kwargs[access.object_param] = str(theirs.pk)
    path = endpoint.path(**kwargs)
    _assert_routes_to(endpoint, path)

    response = client.get(path)
    status = response.status_code
    assert not 200 <= status < 300, (
        f"{endpoint.key} is staff-only, but a vendor member got {status}"
    )
    _assert_no_markers(endpoint, response, tenants)
    if theirs is None:
        return

    missing = client.get(endpoint.path(**{**kwargs, access.object_param: "0"}))
    assert missing.status_code == status, (
        f"{endpoint.key}: a vendor member gets {status} for an existing object and "
        f"{missing.status_code} for a missing one, which lets them enumerate objects"
    )
    staff_client = Client()
    verified_login(staff_client, UserFactory.create(superuser=True))
    assert staff_client.get(path).status_code != 404, (
        f"{endpoint.key}: staff get 404 for the object too, so the URL or the object maker "
        "is wrong and the check proves nothing"
    )


def _check_no_vendor_data(
    client: Client, endpoint: Endpoint, access: NoVendorData, tenants: CrossTenant, method: str
) -> None:
    seeded = tenants.seed_b()  # so that a page leaking vendor data has something to leak
    headers = _login(access)(client, tenants)
    path = access.path or _sample_path(endpoint, access.kwargs)
    _assert_routes_to(endpoint, path)
    _assert_no_markers(endpoint, _request(client, method, path, headers), tenants, *seeded)
