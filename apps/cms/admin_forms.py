"""The editors' (Wagtail) login form, accessible like the staff login (CMP.9, WCAG 2.2 SC 3.3.8).

Wagtail's own form gives the username and password fields no `autocomplete` token; this one adds
them, the show-password toggle and errors tied to the fields (`WAGTAILADMIN_USER_LOGIN_FORM`).
"""

from wagtail.admin.forms.auth import LoginForm

from apps.accounts.forms import AccessibleLoginMixin


class EditorLoginForm(AccessibleLoginMixin, LoginForm):  # type: ignore[misc]  # Wagtail is untyped
    """Wagtail's login form with the CMP.9 attributes."""
