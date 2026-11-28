"""Manager shop space: the shop's own AI provider and key (ING.14, owner decision D-13).

Owner only. Setting the key is the group 2 session action `ai_provider_key` (CORE.25): a passkey
window or a passkey once. The key is write-only: the page never renders it. Thin adapter over
`apps.ingest.provider_services`.
"""

from dataclasses import replace
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_post_parameters

from apps.accounts.decorators import requires_level_method
from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.ingest import provider_services as services
from apps.ingest.models import VendorAIProvider

from .forms import AIProviderForm
from .views import ManagerView

SECTION = "shop.ai_provider"


class AIProviderView(ManagerView):
    section = SECTION
    template_name = "manager/shop/ai_provider.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        row = self.scoped(VendorAIProvider).first()
        context.setdefault(
            "form",
            AIProviderForm(initial={"provider": row.provider, "model": row.model} if row else {}),
        )
        context["has_key"] = bool(row and row.has_key)
        return context

    # The key never reaches an error report (500 page, tracker): Django hides it from the POST.
    # Group 2 (CORE.25); the shop owner confirms it, once or in a window.
    @method_decorator(sensitive_post_parameters("api_key"))
    @requires_level_method("ai_provider_key", shop=lambda view, request: view.manager.vendor)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor, actor = self.manager.vendor, self.manager.user
        if vendor is None or not isinstance(actor, User):
            raise Http404("No vendor selected.")
        form = AIProviderForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        data = form.cleaned_data
        try:
            services.save_provider(
                vendor,
                provider=data["provider"],
                model=data["model"],
                api_key=data["api_key"],
                remove_key=data["remove_key"],
                actor=actor,
                ctx=ctx,
            )
        except ValidationError as error:
            for name, messages_ in error.message_dict.items():
                form.add_error(name if name in form.fields else None, messages_)
            return self.render_to_response(self.get_context_data(form=form))
        except PermissionDenied:
            return self.render_to_response(self.get_context_data(form=form), status=403)
        messages.success(request, _("Your AI provider is saved."))
        return HttpResponseRedirect(reverse("manager:shop:ai_provider"))
