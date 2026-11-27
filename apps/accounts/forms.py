"""Login forms that meet WCAG 2.2 SC 3.3.8, accessible authentication (CMP.9, spec 14 §14.6).

Password managers and the browser fill the fields (`autocomplete` tokens), the password field
gets the show-password toggle of `accounts/password-toggle.js`, and a failed login marks both
fields invalid and points them at the error text. Authentication itself is unchanged: the
lockout and captcha of CORE.5 run inside `authenticate()` (`backends.LoginProtectionBackend`).
"""

import math
from typing import Any

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext, npgettext

from apps.core.http import client_ip

from .services import login_retry_after

# The id of the element that shows a failed login's error, in every login template.
LOGIN_ERROR_ID = "login-error"


class AccessibleLoginMixin(forms.Form):
    """Autocomplete tokens, the show-password toggle and errors tied to the fields."""

    lock_message: str = ""  # set by `clean()` when the sign-in is locked (CORE.29)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["autocomplete"] = "username"
        password = self.fields["password"].widget.attrs
        password["autocomplete"] = "current-password"
        password["data-password-toggle"] = _("Show password")  # the toggle button's text

    def clean(self) -> Any:
        """Say how long a locked login must wait (CORE.29, owner decision Q-12).

        The lock is keyed on what was typed, so a real and an unknown email get the same text
        and the same status. The check runs before any password check, so it adds no timing
        difference either.
        """
        username = self.cleaned_data.get("username")
        if username and self.cleaned_data.get("password"):
            request = getattr(self, "request", None)
            ip = client_ip(request) if request is not None else None
            wait = login_retry_after(username, ip)
            if wait:
                minutes = max(1, math.ceil(wait.total_seconds() / 60))
                self.lock_message = self.wait_text(minutes)
                raise ValidationError(self.lock_message, code="locked")
        return super().clean()

    @staticmethod
    def wait_text(minutes: int) -> str:
        """The Manager login is shared with customers: informal register. Staff override it."""
        return ngettext(
            "Too many attempts. Please try again in %(minutes)s minute.",
            "Too many attempts. Please try again in %(minutes)s minutes.",
            minutes,
        ) % {"minutes": minutes}

    def full_clean(self) -> None:
        super().full_clean()
        if self.is_bound and self.errors:
            for name in ("username", "password"):
                attrs = self.fields[name].widget.attrs
                attrs["aria-invalid"] = "true"
                attrs["aria-describedby"] = LOGIN_ERROR_ID


class StaffLoginForm(AccessibleLoginMixin, AdminAuthenticationForm):
    """The Django admin login (`admin.site.login_form`)."""

    @staticmethod
    def wait_text(minutes: int) -> str:
        """Formal register (staff), with its own gettext context."""
        return npgettext(
            "staff sign-in",
            "Too many attempts. Please try again in %(minutes)s minute.",
            "Too many attempts. Please try again in %(minutes)s minutes.",
            minutes,
        ) % {"minutes": minutes}


class ManagerLoginForm(AccessibleLoginMixin, AuthenticationForm):
    """The Manager login (`manager.views.LoginView`): the same tokens, toggle and error ties."""
