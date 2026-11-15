"""The account's sessions (CORE.24): the list, "Log out everywhere else" and ending one session.

Thin adapters over `services`. Ending is a POST with CSRF; a session is named by the public id
of its index row, never by its key, and only the user's own sessions are found.
"""

import uuid

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_GET, require_POST

from apps.core.audit import audit_context
from apps.core.public_forms import not_anonymous

from . import services
from .mfa_views import signed_in
from .models import User


def _user(request: HttpRequest) -> User:
    user = request.user
    assert isinstance(user, User)  # noqa: S101  # guaranteed by `signed_in`
    return user


@signed_in
@require_GET
def sessions(request: HttpRequest) -> HttpResponse:
    current = request.session.session_key
    rows = [
        {"session": row, "current": row.session_key == current}
        for row in services.user_sessions(_user(request))
    ]
    return render(
        request,
        "accounts/sessions/list.html",
        {"rows": rows, "others": sum(1 for row in rows if not row["current"])},
    )


@not_anonymous(
    reason="signed in: ends the caller's own other sessions only",
    purpose="Log out everywhere else",
    task="CORE.24",
)
@signed_in
@require_POST
def end_others(request: HttpRequest) -> HttpResponse:
    changes = services.end_sessions(
        _user(request),
        keep_session_key=request.session.session_key,
        reason="others",
        ctx=audit_context(request),
    )
    if changes.is_empty:
        messages.info(request, gettext("There were no other sessions."))
    else:
        messages.success(request, gettext("You are signed out everywhere else."))
    return HttpResponseRedirect(reverse("sessions:list"))


@not_anonymous(
    reason="signed in: the session is looked up among the caller's own",
    purpose="End one session",
    task="CORE.24",
)
@signed_in
@require_POST
def end_one(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    user = _user(request)
    own = {str(row.public_id): row for row in services.user_sessions(user)}
    row = own.get(str(public_id))
    if row is None:
        raise Http404
    current = row.session_key == request.session.session_key
    services.end_sessions(user, reason="one", only=str(public_id), ctx=audit_context(request))
    if current:  # the user ended the session they are using: they are signed out now
        return HttpResponseRedirect(reverse("login"))
    messages.success(request, gettext("The session has ended."))
    return HttpResponseRedirect(reverse("sessions:list"))
