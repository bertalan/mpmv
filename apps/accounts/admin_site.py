"""The staff admin site: Django's, with the self-service password change audited (SEC.4).

`/django-admin/password_change/` is not a ModelAdmin view, so the per-model audit hooks in
`admin.py` (and the core.E201-E203 checks) never see it. Its view is replaced here by one that
records `accounts.user.password_changed` in the same transaction as the new password.
"""

from typing import Any, cast

from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminPasswordChangeForm
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import PasswordChangeView
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.core.audit import audit_context
from apps.core.public_forms import Protection, public_form

from . import levels, services
from .models import User


class AuditedPasswordChangeView(PasswordChangeView):
    """Django's password change view; a successful change is audited without the hash."""

    def form_valid(self, form: PasswordChangeForm) -> HttpResponse:
        user: User = form.user
        had_usable_password = user.has_usable_password()
        with transaction.atomic():
            response = super().form_valid(form)
            services.record_password_change(
                user, had_usable_password=had_usable_password, ctx=audit_context(self.request)
            )
        # The password is a credential change: close the passkey window (the key was cycled).
        levels.close_window(self.request, "password")
        return response


class AuditedAdminSite(AdminSite):
    @public_form(
        protection=Protection.CAPTCHA,
        purpose="Sign in to the staff admin",
        task="CORE.5",
        rate_limit="per account and IP lockout (CORE.5)",
        templates=("accounts/admin/login.html",),
    )
    def login(self, request: HttpRequest, extra_context: dict[str, Any] | None = None) -> Any:
        return super().login(request, extra_context)

    def password_change(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> TemplateResponse:
        # Mirrors `AdminSite.password_change` (Django 6.1), with the audited view.
        defaults: dict[str, Any] = {
            "form_class": self.password_change_form or AdminPasswordChangeForm,
            "success_url": reverse("admin:password_change_done", current_app=self.name),
            "extra_context": {**self.each_context(request), **(extra_context or {})},
        }
        if self.password_change_template is not None:
            defaults["template_name"] = self.password_change_template
        request.current_app = self.name
        return cast(TemplateResponse, AuditedPasswordChangeView.as_view(**defaults)(request))
