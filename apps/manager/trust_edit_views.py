"""Manager: a new trust rules version with a dry-run preview (TRU.1 part 2, slice 2).

Same section as the read-only pages (`platform.trust_rules`: configuration role and platform
owner). A POST first shows the dry-run ChangeSet of `apps.trust.services.add_trust_rules`: the
vendors checked and changed and the thresholds that differ from the version in force; nothing is
written. Confirming posts the same fields again with `confirm` and writes through the service,
which audits (no second entry here). The POST needs the session level of `operator_config`.
"""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.decorators import requires_level_method
from apps.core.audit import audit_context
from apps.trust import engine, services
from apps.trust.models import TrustRules

from .fitment_views import echo
from .trust_forms import TrustRulesForm
from .trust_views import GROUPS, SECTION, format_value
from .views import ManagerView


def build_impact_inputs() -> list[engine.VendorTrustInputs]:
    """One `VendorTrustInputs` per vendor for the impact counts. Building them from orders,
    reviews and returns is TRU.2: until it lands the preview checks no vendor (counts at 0)."""
    return []


def in_force_data() -> tuple[engine.TrustRulesData, TrustRules | None]:
    """The thresholds the form starts from: the version in force, else the latest, else the spec
    defaults (nothing was ever saved)."""
    rules: TrustRules | None
    try:
        rules = services.rules_in_force()
    except services.NoValidRow:
        rules = TrustRules.objects.order_by("-version").first()
    if rules is None:
        return engine.TrustRulesData(version=1), None
    return rules.to_data(), rules


def soon() -> dt.datetime:
    """The suggested start: a few minutes ahead, so the default is never "in the past"."""
    return (timezone.now() + dt.timedelta(minutes=5)).replace(second=0, microsecond=0)


class TrustRuleNewView(ManagerView):
    section = SECTION
    template_name = "manager/platform/trust_rule_new.html"
    preview_template_name = "manager/platform/trust_rule_preview.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Staff-entered dates are read and shown in the business time zone (VEN.9 rule); the
        service compares the converted, aware value in UTC."""
        with timezone.override(ZoneInfo(settings.BUSINESS_TIME_ZONE)):
            response = super().dispatch(request, *args, **kwargs)
            if isinstance(response, TemplateResponse):
                response.render()  # the template renders lazily: do it inside the zone
            return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        current, _rules = in_force_data()
        form = kwargs.get("form") or TrustRulesForm(
            current=current,
            initial={"valid_from": soon()},
        )
        context.update(form=form, groups=form.groups(), title=_("New trust rules version"))
        return context

    @requires_level_method("operator_config")  # group 2: a passkey once, or an open window
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        current, _rules = in_force_data()
        form = TrustRulesForm(request.POST, current=current)
        confirmed = request.POST.get("confirm") == "1"
        if form.is_valid():
            try:
                changes = services.add_trust_rules(
                    form.params(),
                    valid_from=form.cleaned_data["valid_from"],
                    note=form.cleaned_data["note"],
                    ctx=audit_context(request),
                    dry_run=not confirmed,
                    impact_inputs=build_impact_inputs(),
                )
            except ValidationError as error:
                self.put_errors(form, error)
            else:
                if confirmed:
                    messages.success(request, _("The new trust rules version is saved."))
                    return HttpResponseRedirect(reverse("manager:platform:trust_rules"))
                return self.render_preview(request, form, changes, current)
        return self.render_to_response(self.get_context_data(form=form))

    @staticmethod
    def put_errors(form: TrustRulesForm, error: ValidationError) -> None:
        """Put the service's messages on the field they name (`name: message` for a threshold)."""
        for key, messages_ in error.message_dict.items():
            for message in messages_:
                name, _sep, rest = message.partition(": ")
                if key == "params" and name in form.fields:
                    form.add_error(name, rest)
                else:
                    form.add_error(key if key in form.fields else None, message)

    def render_preview(
        self, request: HttpRequest, form: TrustRulesForm, changes: Any, current: Any
    ) -> HttpResponse:
        created = next(change for change in changes.changes if change.op == "create")
        after = created.after or {}
        new = engine.rules_from_params(current.version, form.params())
        diff = [
            {
                "heading": heading,
                "label": label,
                "before": format_value(getattr(current, name), unit),
                "after": format_value(getattr(new, name), unit),
            }
            for heading, rows in GROUPS
            for name, label, unit in rows
            if getattr(current, name) != getattr(new, name)
        ]
        context = self.get_context_data(form=form)
        context.update(
            title=_("Preview of the new trust rules version"),
            version=after.get("version"),
            valid_from=form.cleaned_data["valid_from"],
            note=form.cleaned_data["note"],
            checked=after.get("vendors_checked", 0),
            changed=after.get("vendors_changed", 0),
            diff=diff,
            echo=echo(request),
            cancel_url=request.path,
        )
        return self.response_class(
            request=request,
            template=[self.preview_template_name],
            context=context,
            using=self.template_engine,
        )
