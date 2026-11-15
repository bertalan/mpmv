"""Sessions in the Manager platform space (CORE.24): thin adapters over `accounts.services`.

- "User sessions" (Support and the platform owner): find an account, see its sessions and "End
  this user's sessions" with a reason.
- "End every session" (the platform owner only): the panic button, confirmed by a fixed word.

Both actions are critical (CORE.25): a fresh passkey each time, even with the passkey window
open. Neither touches API keys or signed links, which are not sessions.
"""

from typing import Any, Final

from django.contrib import messages
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.accounts import services as accounts_services
from apps.accounts.decorators import requires_level_method
from apps.accounts.models import User
from apps.core.audit import audit_context

from .forms import EndEverySessionForm, EndUserSessionsForm, UserLookupForm
from .views import ManagerView

USER_SESSIONS_SECTION: Final = "platform.user_sessions"  # a row of roles.MATRIX
PANIC_SECTION: Final = "platform.sessions_panic"  # a row of roles.MATRIX
# The word the platform owner types, in the language of the page.
PANIC_WORD: Final = gettext_noop("END EVERY SESSION")


def _actor(view: ManagerView) -> User:
    actor = view.manager.user
    if not isinstance(actor, User):
        raise Http404("No Manager page here.")
    return actor


class UserSessionsView(ManagerView):
    """A user's page for Support: their sessions, and "End this user's sessions"."""

    section = USER_SESSIONS_SECTION
    template_name = "manager/platform/user_sessions.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        target = kwargs.get("target")
        public_id = self.request.GET.get("user", "")
        if target is None and public_id:  # the account is named by its public id, never its email
            try:
                target = User.objects.filter(public_id=public_id).first()
            except ValidationError:
                target = None
        context.setdefault("lookup", UserLookupForm())
        context["target"] = target
        if target is not None:
            context["sessions"] = accounts_services.user_sessions(target)
            context.setdefault("form", EndUserSessionsForm(initial={"user": target.public_id}))
        return context

    @requires_level_method("sessions_end_user")  # critical (CORE.25)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        actor = _actor(self)
        form = EndUserSessionsForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        target = User.objects.filter(public_id=form.cleaned_data["user"]).first()
        if target is None:
            raise Http404("No such account.")
        try:
            changes = accounts_services.support_end_sessions(
                target, actor=actor, reason=form.cleaned_data["reason"], ctx=audit_context(request)
            )
        except ValidationError as error:
            form.add_error("reason", " ".join(error.messages))
            context = self.get_context_data(form=form, target=target)
            return self.render_to_response(context, status=400)
        except PermissionDenied:
            raise Http404("No such action.") from None
        if changes.is_empty:
            messages.info(request, _("That account had no session to end."))
        else:
            messages.success(request, _("Every session of that account has ended."))
        url = reverse("manager:platform:user_sessions")
        if target != actor:  # Support who ended their own sessions is signed out now
            url += f"?user={target.public_id}"
        return HttpResponseRedirect(url)


class UserLookupView(ManagerView):
    """Find the account by email: a POST with CSRF, so the email never reaches a URL (proxy logs,
    browser history); the answer redirects to the page with the account's public id."""

    section = USER_SESSIONS_SECTION
    template_name = "manager/platform/user_sessions.html"
    http_method_names = ["post"]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("lookup", UserLookupForm())
        context["target"] = None
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        lookup = UserLookupForm(request.POST)
        target = None
        if lookup.is_valid():
            target = User.objects.filter(email__iexact=lookup.cleaned_data["email"]).first()
            if target is None:
                lookup.add_error("email", _("There is no account with that email."))
        if target is None:
            return self.render_to_response(self.get_context_data(lookup=lookup), status=400)
        url = reverse("manager:platform:user_sessions")
        return HttpResponseRedirect(f"{url}?user={target.public_id}")


class EndEverySessionView(ManagerView):
    """The panic button: every session of every user ends, but the caller's current one."""

    section = PANIC_SECTION
    template_name = "manager/platform/sessions_panic.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("form", EndEverySessionForm())
        context["word"] = _(PANIC_WORD)
        context["live"] = Session.objects.filter(expire_date__gt=timezone.now()).count()
        return context

    @requires_level_method("sessions_end_all")  # critical (CORE.25), on the floor
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        actor = _actor(self)
        form = EndEverySessionForm(request.POST)
        if form.is_valid() and form.cleaned_data["confirm"].strip() != _(PANIC_WORD):
            form.add_error("confirm", _("Type the words exactly as shown."))
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        try:
            accounts_services.end_all_sessions(
                actor=actor,
                keep_session_key=request.session.session_key,
                ctx=audit_context(request),
            )
        except PermissionDenied:
            raise Http404("No such action.") from None
        messages.success(request, _("Every other session has ended. The platform staff were told."))
        return HttpResponseRedirect(reverse("manager:platform:sessions_panic"))
