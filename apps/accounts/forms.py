"""Login forms that meet WCAG 2.2 SC 3.3.8, accessible authentication (CMP.9, spec 14 §14.6).

Password managers and the browser fill the fields (`autocomplete` tokens), the password field
gets the show-password toggle of `accounts/password-toggle.js`, and a failed login marks both
fields invalid and points them at the error text. Authentication itself is unchanged: the
lockout and captcha of CORE.5 run inside `authenticate()` (`backends.LoginProtectionBackend`).
"""

from typing import Any

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.utils.translation import gettext_lazy as _

# The id of the element that shows a failed login's error, in every login template.
LOGIN_ERROR_ID = "login-error"


class AccessibleLoginMixin(forms.Form):
    """Autocomplete tokens, the show-password toggle and errors tied to the fields."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["autocomplete"] = "username"
        password = self.fields["password"].widget.attrs
        password["autocomplete"] = "current-password"
        password["data-password-toggle"] = _("Show password")  # the toggle button's text

    def full_clean(self) -> None:
        super().full_clean()
        if self.is_bound and self.errors:
            for name in ("username", "password"):
                attrs = self.fields[name].widget.attrs
                attrs["aria-invalid"] = "true"
                attrs["aria-describedby"] = LOGIN_ERROR_ID


class StaffLoginForm(AccessibleLoginMixin, AdminAuthenticationForm):
    """The Django admin login (`admin.site.login_form`)."""
