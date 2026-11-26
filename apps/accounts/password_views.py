"""The forced password change after a breached-password sign-in (CORE.28).

`BreachedPasswordMiddleware` sends a flagged user here before any other page. The form is
Django's password change form: the current password, then the new one twice, which goes through
`AUTH_PASSWORD_VALIDATORS` (the breach check included). The change is audited like any other
(`record_password_change`, which also clears the flag and ends the user's other sessions). A
role that must use MFA then verifies the second factor again before going on.
"""

from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.forms import PasswordChangeForm
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.urls import reverse

from apps.core.public_forms import not_anonymous

from .admin_site import AuditedPasswordChangeView
from .http import safe_next
from .mfa_session import drop_verification
from .mfa_views import signed_in
from .models import User
from .services import mfa_required


class BreachedPasswordChangeView(AuditedPasswordChangeView):
    template_name = "accounts/password/change.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["breached"] = isinstance(user, User) and user.password_breached_at is not None
        context["next"] = safe_next(self.request)
        return context

    def form_valid(self, form: PasswordChangeForm) -> HttpResponse:
        privileged = mfa_required(form.user)
        response = super().form_valid(form)
        if privileged:
            drop_verification(self.request)
        return response

    def get_success_url(self) -> str:
        next_url = safe_next(self.request)
        if mfa_required(self.request.user):
            return f"{reverse('mfa:verify')}?{urlencode({'next': next_url})}"
        return next_url


@not_anonymous(
    reason="signed in: changes the caller's own password after the current one",
    purpose="Change your password (forced after a breached-password sign-in)",
    task="CORE.28",
)
@signed_in
def change(request: HttpRequest) -> HttpResponseBase:
    return BreachedPasswordChangeView.as_view()(request)
