"""SEC.4: one helper turns a web request into the AuditContext every adapter passes on.

Coherence check 3, finding 1: the MFA views recorded `REMOTE_ADDR` (the reverse proxy) while
the admins used the trusted-proxy client IP. Every request-based context now comes from
`apps.core.audit.audit_context`, which uses `apps.core.http.client_ip`.
"""

import ast
from pathlib import Path

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from apps.accounts.models import User
from apps.core.audit import audit_context, user_context

PROXY = "10.89.250.2"
CLIENT = "203.0.113.7"
BEHIND_PROXY = {"TRUSTED_PROXY_HEADER": "X-Real-IP", "TRUSTED_PROXIES": ["10.89.250.0/24"]}
APPS = Path(__file__).resolve().parents[3]


def request_as(user: User | AnonymousUser, remote_addr: str, client: str = CLIENT) -> HttpRequest:
    request = RequestFactory().post("/", headers={"X-Real-IP": client})
    request.META["REMOTE_ADDR"] = remote_addr
    request.user = user
    return request


def unsaved_user(*, staff: bool) -> User:
    user = User(email="someone@example.test", is_staff=staff)
    user.pk = 7
    return user


@override_settings(**BEHIND_PROXY)
def test_behind_the_trusted_proxy_the_context_records_the_client_ip() -> None:
    ctx = audit_context(request_as(unsaved_user(staff=True), PROXY))
    assert ctx.ip == CLIENT


@override_settings(**BEHIND_PROXY)
def test_a_header_from_an_untrusted_sender_is_ignored() -> None:
    ctx = audit_context(request_as(unsaved_user(staff=False), CLIENT, client="198.51.100.9"))
    assert ctx.ip == CLIENT


def test_staff_act_from_the_admin() -> None:
    ctx = audit_context(request_as(unsaved_user(staff=True), CLIENT))
    assert ctx.actor_id == 7
    assert ctx.actor_label == "staff:someone@example.test"
    assert ctx.source == "admin"


def test_other_users_act_on_their_own_account() -> None:
    ctx = user_context(unsaved_user(staff=False), CLIENT)
    assert ctx.actor_label == "user:someone@example.test"
    assert ctx.source == "system"
    assert ctx.ip == CLIENT


def test_no_adapter_defines_its_own_request_context() -> None:
    """Only the API keeps its own helper: its actor is the API client, not a user."""
    allowed = {APPS / "core" / "audit.py", APPS / "api" / "v1" / "mutations.py"}
    offenders = []
    for path in APPS.rglob("*.py"):
        if "tests" in path.parts or path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {"audit_context", "user_context"}:
                offenders.append(f"{path.relative_to(APPS)}:{node.lineno}")
    assert offenders == []
