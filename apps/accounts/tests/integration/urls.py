"""Test-only URLconf: the project's routes plus test views behind the account decorators.

- A stand-in registration view behind `protect_registration` (CORE.5). No registration view
  exists yet; the one that comes with customer accounts uses this decorator.
- Views protected by `step_up_required` (CORE.4).
- A stand-in "approve a document" action page reached by secure action links (CORE.15): the
  `tests.approve` purpose, with documents kept in memory (`DOCUMENTS`). Its approve POST
  re-checks the version, as every action page must.
- A stand-in "claim an invitation" page reached by email-bound links (VEN.24): the
  `tests.invite` purpose, bound to an email address instead of a user (`INVITATIONS`).
"""

from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.template import RequestContext, Template
from django.urls import path, reverse

from apps.accounts.decorators import protect_registration, requires_level, step_up_required
from apps.accounts.models import User
from apps.accounts.services import (
    ACTION_VERSION_PARAM,
    ActionPurpose,
    action_version_matches,
    email_bound_link,
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


# --- CORE.25: one view per action group (D-32)


@requires_level("catalogue_edit")
def everyday(request: HttpRequest) -> HttpResponse:
    return HttpResponse("everyday")


@requires_level("export")
def export(request: HttpRequest) -> HttpResponse:
    return HttpResponse("export-csv")


@requires_level("export")
def export_json(request: HttpRequest) -> HttpResponse:
    return HttpResponse(f"export-json {len(request.body)}")


@requires_level("export")
def export_upload(request: HttpRequest) -> HttpResponse:
    return HttpResponse("export-upload")


@requires_level("backup")
def backup(request: HttpRequest) -> HttpResponse:
    return HttpResponse("backup")


@requires_level("payout_details")
def payout(request: HttpRequest) -> HttpResponse:
    return HttpResponse("payout")


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


# --- VEN.24: an email-bound purpose (the recipient has no account yet)


@dataclass
class Invitation:
    email: str
    version: str


INVITATIONS: dict[str, Invitation] = {}
INVITE_PURPOSE = "tests.invite"


def _invitation_version(key: str) -> str | None:
    invitation = INVITATIONS.get(key)
    return invitation.version if invitation else None


def _invitation_email(key: str) -> str | None:
    invitation = INVITATIONS.get(key)
    return invitation.email if invitation else None


register_action_purpose(
    ActionPurpose(
        name=INVITE_PURPOSE,
        version_of=_invitation_version,
        url_of=lambda key: reverse("test-invite", args=[key]),
        email_of=_invitation_email,
    )
)


def invite(request: HttpRequest, key: str) -> HttpResponse:
    check = email_bound_link(request.session, INVITE_PURPOSE, key)
    if check.refusal is not None:
        return HttpResponse(f"refused {check.refusal}", status=403)
    return HttpResponse(f"invitation {key}")


urlpatterns = [
    path("register/", register, name="test-register"),
    path("test/sensitive/", sensitive, name="test-sensitive"),
    path("test/very-sensitive/", very_sensitive, name="test-very-sensitive"),
    path("test/everyday/", everyday, name="test-everyday"),
    path("test/export/", export, name="test-export"),
    path("test/export-json/", export_json, name="test-export-json"),
    path("test/export-upload/", export_upload, name="test-export-upload"),
    path("test/backup/", backup, name="test-backup"),
    path("test/payout/", payout, name="test-payout"),
    path("test/approve/<str:key>/", approve, name="test-approve"),
    path("test/invite/<str:key>/", invite, name="test-invite"),
    *project_urlpatterns,
]
