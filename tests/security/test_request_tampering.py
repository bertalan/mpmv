"""SEC.5 attack scenarios, category "Request tampering" (owner security wave, item 10).

Each test sends a tampered request (extra fields, a hostile `next`, a forged Host, a framing
page's view, a method override) and asserts that it changes nothing it should not. Cases an
existing test already covers are not repeated here: tests/security/README.md names the covering
test.
"""

import importlib
import re
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import pytest
from django.conf import settings
from django.contrib import admin
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login, totp_token, verified_login
from apps.api.tests.factories import ApiClientFactory
from apps.api.v1.api import api
from apps.catalog.models import Product, ProductStatus
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.factory_registry import app_factories, factory_model

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_open")]  # OPS.5

BASE_DIR = Path(__file__).resolve().parents[2]
HOST = "testserver"
EVIL = "evil.example"


# --- Mass assignment: extra vendor, is_staff, price, status or owner fields

# Field names an API client must never set through a request body. `vendor` is allowed only on
# the operations listed in VENDOR_SCOPED_BODIES, each scoped to the caller (CORE.3, CORE.13).
PRIVILEGED_FIELDS = frozenset(
    {
        "is_staff",
        "is_superuser",
        "is_active",
        "groups",
        "user_permissions",
        "password",
        "owner",
        "user",
        "status",
        "vendor_id",
        "created_by",
        "public_id",
        "id",
        "pk",
    }
)
VENDOR_SCOPED_BODIES = {
    # Covered by apps/api/tests/integration/test_v1_catalog.py::
    # test_a_vendor_key_cannot_create_for_another_vendor (404, nothing created).
    ("post", "/api/v1/catalog/products"),
}


def _schema_properties(schema: dict[str, Any], components: dict[str, Any]) -> set[str]:
    """Every property name a JSON schema accepts, following $ref, arrays and combinators."""
    names: set[str] = set()
    stack: list[Any] = [schema]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in seen:
                continue
            seen.add(ref)
            stack.append(components[ref.rsplit("/", 1)[-1]])
            continue
        properties = node.get("properties", {})
        names.update(properties)
        stack.extend(properties.values())
        stack.append(node.get("items"))
        stack.append(node.get("additionalProperties"))
        for key in ("anyOf", "allOf", "oneOf"):
            stack.extend(node.get(key, []))
    return names


def _request_bodies() -> dict[tuple[str, str], set[str]]:
    schema = api.get_openapi_schema()
    components = schema.get("components", {}).get("schemas", {})
    bodies: dict[tuple[str, str], set[str]] = {}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            content = operation.get("requestBody", {}).get("content", {})
            for media in content.values():
                bodies[(method, path)] = _schema_properties(media.get("schema", {}), components)
    return bodies


def test_no_api_request_body_accepts_a_privileged_field() -> None:
    bodies = _request_bodies()
    assert bodies, "no API operation with a request body found: the sweep checks nothing"
    offending = {op: sorted(names & PRIVILEGED_FIELDS) for op, names in bodies.items()}
    assert {op: names for op, names in offending.items() if names} == {}
    with_vendor = {op for op, names in bodies.items() if "vendor" in names}
    assert with_vendor <= VENDOR_SCOPED_BODIES, with_vendor - VENDOR_SCOPED_BODIES


def test_extra_fields_in_an_api_payload_are_ignored(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=["catalog:read", "catalog:write"]
    )
    forged_id = uuid.uuid4()
    payload = {
        "sku": "MASS-1",
        "price_net": "10.00",
        "translations": [{"language": "en", "title": "Mug", "status": "published"}],
        # The attack: fields the schema does not declare.
        "status": ProductStatus.PUBLISHED,
        "is_staff": True,
        "owner": str(uuid.uuid4()),
        "vendor_id": other.pk,
        "id": 424242,
        "public_id": str(forged_id),
    }
    response = client.post(
        "/api/v1/catalog/products",
        payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 201, response.content
    product = Product.objects.get()
    assert product.vendor == vendor
    assert product.status == ProductStatus.DRAFT
    assert product.pk != 424242
    assert product.public_id != forged_id
    assert AuditLog.objects.filter(
        source=AuditLog.Source.API, target_key=str(product.public_id)
    ).exists()


def _admin_request(user: User) -> HttpRequest:
    request = RequestFactory().get("/django-admin/")
    request.user = user
    SessionMiddleware(lambda _: HttpResponse()).process_request(request)
    request.session.save()
    return request


def _instances() -> dict[type[models.Model], models.Model]:
    """One saved object per model that has a factory, for the change forms."""
    found: dict[type[models.Model], models.Model] = {}
    for factory_class in app_factories():
        model = factory_model(factory_class)
        if model in admin.site._registry and model not in found:
            found[model] = factory_class.create()
    return found


def test_no_admin_form_accepts_a_field_it_shows_read_only() -> None:
    """A POSTed read-only field (staff access flags, a product's status...) is never bound.

    Every ModelAdmin, for a superuser and for a staff member holding every permission, on the
    add form and on a change form.
    """
    superuser = UserFactory.create(superuser=True)
    staff = UserFactory.create(staff=True)
    instances = _instances()
    leaks: dict[str, list[str]] = {}
    for user in (superuser, staff):
        request = _admin_request(user)
        for model, model_admin in admin.site._registry.items():
            if not model.__module__.startswith("apps."):
                continue  # third-party admins (task queue, sessions...) are read-only views
            for obj in (None, instances.get(model)):
                if obj is None and model in instances:
                    continue  # the change form is checked instead of the add form
                form = model_admin.get_form(request, obj, change=obj is not None)
                read_only = set(model_admin.get_readonly_fields(request, obj))
                if bound := sorted(read_only & set(form.base_fields)):
                    leaks[f"{model._meta.label} ({user.email})"] = bound
    assert leaks == {}


# --- Open redirect through `next`

OPEN_REDIRECTS = [
    f"//{EVIL}/",
    f"///{EVIL}/",
    f"/\\{EVIL}/",
    f"\\\\{EVIL}/",
    f"\\/{EVIL}/",
    f"https:{EVIL}",
    f"https:/{EVIL}",
    f"https://{EVIL}/",
    f"http://{HOST}@{EVIL}/",
    f"/\t/{EVIL}/",
    f" //{EVIL}/",
    f"%2F%2F{EVIL}/",
    f"/%2F%2F{EVIL}/",
    f"//{EVIL}%2F@{HOST}/",
    "javascript:alert(document.domain)",
    f"data:text/html,<script>location='https://{EVIL}'</script>",
]


def _browser_target(location: str, page: str) -> str | None:
    """Where a browser goes for this Location header on `page`: the host, or None if no web URL.

    Browsers strip tabs and newlines and read a backslash as a slash in http(s) URLs.
    """
    cleaned = re.sub(r"[\t\r\n]", "", location.strip()).replace("\\", "/")
    target = urlsplit(urljoin(f"http://{HOST}{page}", cleaned))
    return target.hostname if target.scheme in {"http", "https"} else None


@pytest.mark.parametrize("next_url", OPEN_REDIRECTS)
def test_the_mfa_verification_never_redirects_off_site(client: Client, next_url: str) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    page = reverse("mfa:verify")
    response = client.post(
        f"{page}?{urlencode({'next': next_url})}",
        {"method": "totp", "token": totp_token(device)},
    )
    assert response.status_code == 302
    assert _browser_target(response["Location"], page) == HOST, response["Location"]


@pytest.mark.parametrize("next_url", OPEN_REDIRECTS)
def test_the_language_switcher_never_redirects_off_site(client: Client, next_url: str) -> None:
    page = reverse("switch_language")
    response = client.post(page, {"language": "it", "next": next_url})
    assert response.status_code == 302
    assert _browser_target(response["Location"], page) == HOST, response["Location"]


@pytest.mark.parametrize("next_url", OPEN_REDIRECTS)
def test_the_login_never_redirects_off_site(client: Client, next_url: str) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(staff=True, password=password)
    page = "/django-admin/login/"
    response = client.post(page, {"username": user.email, "password": password, "next": next_url})
    assert response.status_code == 302
    assert _browser_target(response["Location"], page) == HOST, response["Location"]
    assert AuditLog.objects.filter(action="accounts.login.succeeded").count() == 1


# --- Host header poisoning of reset and action links


def test_a_forged_host_header_is_refused(client: Client) -> None:
    response = client.get("/django-admin/login/", HTTP_HOST=EVIL)
    assert response.status_code == 400
    assert EVIL not in response.content.decode()


def test_a_forwarded_host_header_is_ignored(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy's X-Forwarded-Host is never trusted: links and redirects keep our host."""
    assert not settings.USE_X_FORWARDED_HOST
    # The live settings refuse the placeholder key that settings.test sets by default, so the
    # test brings its own generated key and never depends on what ran before it.
    # base is already imported with the placeholder, so patch its value; the env var follows suit.
    key = secrets.token_urlsafe(50)
    monkeypatch.setenv("DJANGO_SECRET_KEY", key)
    monkeypatch.setattr("mpmv.settings.base.SECRET_KEY", key)
    for module in ("mpmv.settings.live", "mpmv.settings.stage"):
        monkeypatch.delitem(sys.modules, module, raising=False)
        assert not getattr(importlib.import_module(module), "USE_X_FORWARDED_HOST", False)
        monkeypatch.delitem(sys.modules, module, raising=False)
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    response = client.post(
        f"{reverse('mfa:verify')}?next=https://{EVIL}/",
        {"method": "totp", "token": totp_token(device)},
        HTTP_X_FORWARDED_HOST=EVIL,
    )
    assert response["Location"] == "/"


@pytest.mark.parametrize("path", ["/django-admin/password_reset/", "/admin/password_reset/"])
def test_no_password_reset_page_is_reachable(client: Client, path: str) -> None:
    """Neither admin offers a reset form, so no reset email can carry a poisoned link."""
    user = UserFactory.create(staff=True)
    for response in (
        client.get(path, HTTP_X_FORWARDED_HOST=EVIL),
        client.post(path, {"email": user.email}, HTTP_X_FORWARDED_HOST=EVIL),
    ):
        # 404, or the staff admin's catch-all sending an anonymous visitor to its login.
        assert response.status_code in {302, 404}
        if response.status_code == 302:
            assert urlsplit(response["Location"]).path == "/django-admin/login/"
    assert not OutboxEmail.objects.exists()
    assert mail.outbox == []


EMAIL_SENDERS = re.compile(r"\b(enqueue_email|send_action_link_email|send_mail|EmailMessage)\(")
REQUEST_HOSTS = re.compile(r"\b(build_absolute_uri|get_host|META\[.HTTP_HOST.\])\b")


def test_emails_build_absolute_urls_from_settings_only() -> None:
    """A module that sends email never takes a host from the request (SITE_BASE_URL only)."""
    senders = [
        path
        for path in (BASE_DIR / "apps").rglob("*.py")
        if "tests" not in path.parts and EMAIL_SENDERS.search(path.read_text())
    ]
    assert senders, "no email-sending module found: the sweep checks nothing"
    offending = [
        str(path.relative_to(BASE_DIR))
        for path in senders
        if REQUEST_HOSTS.search(path.read_text())
    ]
    assert offending == []


# --- Clickjacking


def _frame_ancestors(response: Any) -> list[str]:
    header: str = response.headers.get("Content-Security-Policy", "")
    for directive in header.split(";"):
        tokens = directive.split()
        if tokens and tokens[0] == "frame-ancestors":
            return tokens[1:]
    return []


def test_no_page_can_be_framed(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user)
    anonymous = Client()
    pages = {
        "home": anonymous.get("/"),
        "not found": anonymous.get("/no-such-page/"),
        "staff login": anonymous.get("/django-admin/login/"),
        "editors login": anonymous.get("/admin/login/"),
        "action link": anonymous.get(reverse("action_link:open", args=["x" * 40])),
        "api": anonymous.get("/api/v1/me"),
        "mfa": client.get(reverse("mfa:overview")),
        "staff admin": client.get("/django-admin/"),
    }
    framable = {
        name: (response.status_code, response.headers.get("X-Frame-Options"))
        for name, response in pages.items()
        if response.headers.get("X-Frame-Options") != "DENY"
        or _frame_ancestors(response) != ["'none'"]
    }
    assert framable == {}


# --- Method override


def test_method_override_headers_and_fields_are_ignored(client: Client) -> None:
    user = UserFactory.create()
    device = verified_login(client, user)
    remove = reverse("mfa:remove_device", args=["totp", device.pk])
    overrides = {
        "X-HTTP-Method-Override": "POST",
        "X-HTTP-Method": "POST",
        "X-Method-Override": "POST",
    }
    assert client.get(f"{remove}?_method=POST", headers=overrides).status_code == 405
    assert TOTPDevice.objects.filter(pk=device.pk).exists()

    get_only = reverse("action_link:open", args=["x" * 40])
    response = client.post(get_only, {"_method": "GET"}, HTTP_X_HTTP_METHOD_OVERRIDE="GET")
    assert response.status_code == 405

    post_only = reverse("switch_language")
    response = client.get(
        f"{post_only}?language=it&next=/&_method=POST", HTTP_X_HTTP_METHOD_OVERRIDE="POST"
    )
    assert response.status_code == 405


def test_the_api_ignores_method_override(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=["catalog:write"])
    response = client.get(
        "/api/v1/catalog/products?_method=POST",
        headers={"Authorization": f"Bearer {key}", "X-HTTP-Method-Override": "POST"},
    )
    assert response.status_code == 405
    assert not Product.objects.exists()


METHOD_OVERRIDE = re.compile(
    r"METHOD_OVERRIDE|Method-Override|[\"']_method[\"']|request\.method\s*=[^=]"
)


def test_no_code_honours_a_method_override() -> None:
    offending = [
        str(path.relative_to(BASE_DIR))
        for root in ("apps", "mpmv")
        for path in (BASE_DIR / root).rglob("*.py")
        if "tests" not in path.parts and METHOD_OVERRIDE.search(path.read_text())
    ]
    assert offending == []
