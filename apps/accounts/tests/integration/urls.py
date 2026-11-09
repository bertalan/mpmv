"""Test-only URLconf: the project's routes plus test views behind the account decorators.

- A stand-in registration view behind `protect_registration` (CORE.5). No registration view
  exists yet; the one that comes with customer accounts uses this decorator.
- Views protected by `step_up_required` (CORE.4).
- A stand-in "approve a document" action page reached by secure action links (CORE.15): the
  `tests.approve` purpose, with documents kept in memory (`DOCUMENTS`). Its approve POST
  re-checks the version, as every action page must.
"""

from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.template import RequestContext, Template
from django.urls import path, reverse

from apps.accounts.decorators import protect_registration, step_up_required
from apps.accounts.models import User
from apps.accounts.services import (
    ACTION_VERSION_PARAM,
    ActionPurpose,
    action_version_matches,
    register_action_purpose,
)
from mpmv.urls import urlpatterns as project_urlpatterns


@protect_registration
def register(request: HttpRequest) -> HttpResponse:
    return HttpResponse("form" if request.method == "GET" else "created")


@step_up_required()
def sensitive(request: HttpRequest) -> HttpResponse:
    return HttpResponse("sensitive")


@step_up_required(max_age=30)
def very_sensitive(request: HttpRequest) -> HttpResponse:
    return HttpResponse("very sensitive")


# --- CORE.15: a stand-in action object and its action page


@dataclass
class Document:
    owner_id: int
    version: str


DOCUMENTS: dict[str, Document] = {}
APPROVED: list[tuple[str, int]] = []  # (document key, approver pk)
APPROVE_PURPOSE = "tests.approve"

APPROVE_PAGE = Template(
    "<!doctype html><title>Approve</title><main><h1>Approve document {{ key }}</h1>"
    '<form method="post">{% csrf_token %}<input type="hidden" name="version" value="{{ version }}">'
    '<button type="submit">Approve</button></form></main>'
)


def _version_of(key: str) -> str | None:
    document = DOCUMENTS.get(key)
    return document.version if document else None


def _may_act(key: str, user: User) -> bool:
    document = DOCUMENTS.get(key)
    return document is not None and document.owner_id == user.pk


register_action_purpose(
    ActionPurpose(
        name=APPROVE_PURPOSE,
        version_of=_version_of,
        url_of=lambda key: reverse("test-approve", args=[key]),
        may_act=_may_act,
    )
)


CHANGED = "This document changed after the link was sent."


@step_up_required()
def approve(request: HttpRequest, key: str) -> HttpResponse:
    """The page shows the version the link was issued for (`?version=`) and puts it in its form;
    the approve POST re-checks it, so a change after the link was opened is never approved."""
    document = DOCUMENTS.get(key)
    if document is None or document.owner_id != request.user.pk:
        return HttpResponse(status=404)
    if request.method == "POST":
        if not action_version_matches(APPROVE_PURPOSE, key, request.POST.get("version")):
            return HttpResponse(CHANGED, status=409)
        APPROVED.append((key, request.user.pk))
        return HttpResponse(f"approved {key}")
    version = request.GET.get(ACTION_VERSION_PARAM) or document.version
    if not action_version_matches(APPROVE_PURPOSE, key, version):
        return HttpResponse(CHANGED, status=409)
    context = RequestContext(request, {"key": key, "version": version})
    return HttpResponse(APPROVE_PAGE.render(context))


urlpatterns = [
    path("register/", register, name="test-register"),
    path("test/sensitive/", sensitive, name="test-sensitive"),
    path("test/very-sensitive/", very_sensitive, name="test-very-sensitive"),
    path("test/approve/<str:key>/", approve, name="test-approve"),
    *project_urlpatterns,
]
