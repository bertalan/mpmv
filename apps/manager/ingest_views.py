"""Manager platform space: supplier sources and the "new vendor from source" wizard (ING.13).

Thin adapter over `apps.ingest.onboarding`. Staff of the Catalogue and moderation role write,
Support and the platform owner read (matrix row `platform.ingest_sources`). The sample file is
parsed in memory by the adapter's dry run and dropped: it is never stored. Size and row limits
are the adapters' own (`base.DEFAULT_MAX_BYTES`). Every write is audited through the services.
"""

import uuid
from typing import Any, Final

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.decorators import requires_level_method
from apps.core.audit import audit_context
from apps.ingest import onboarding
from apps.ingest.adapters import base
from apps.ingest.models import SupplierSource
from apps.vendors.models import Vendor

from .fitment_views import WriteGate, writer
from .views import ManagerView

SECTION: Final = "platform.ingest_sources"  # a row of roles.MATRIX
GATE_ACTION: Final = "catalogue_edit"
LIST_URL: Final = "manager:platform:ingest_sources"


class SourceForm(forms.Form):
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),
        label=gettext_lazy("Vendor"),
        empty_label=gettext_lazy("Choose a vendor"),
    )
    code = forms.SlugField(
        label=gettext_lazy("Name of the source"),
        max_length=60,
        help_text=gettext_lazy("A short name without spaces, for example price-list."),
    )
    adapter_code = forms.ChoiceField(
        label=gettext_lazy("Type of file"),
        choices=[(code, label) for code, (label, _cls) in onboarding.ADAPTERS.items()],
    )
    decimal = forms.ChoiceField(
        label=gettext_lazy("Decimal separator of the numbers"),
        required=False,
        choices=[
            ("", gettext_lazy("Detect it")),
            (",", gettext_lazy("Comma")),
            (".", gettext_lazy("Point")),
        ],
        help_text=gettext_lazy(
            "Choose it when the prices use a separator that is easy to misread."
        ),
    )
    sample = forms.FileField(
        label=gettext_lazy("Sample file"),
        help_text=gettext_lazy(
            "A real export of the supplier. It is read once to show you the result and is not kept."
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Vendor.objects.order_by("display_name")  # type: ignore[attr-defined]

    def clean_sample(self) -> Any:
        sample = self.cleaned_data["sample"]
        if sample.size > base.DEFAULT_MAX_BYTES:
            raise ValidationError(_("The file is larger than the allowed size."))
        return sample


class SourcesView(ManagerView):
    section = SECTION
    template_name = "manager/platform/ingest_sources.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["sources"] = self.scoped(SupplierSource).select_related("vendor")
        context["can_write"] = writer(SECTION)(self.manager)
        return context


class SourceNewView(WriteGate, ManagerView):
    """Step 1: the vendor, the adapter and a sample. "Check" only reads; "Create" also saves an
    inactive source and what the sample showed, both audited."""

    section = SECTION
    write_section = SECTION
    refuse_read_only = True
    template_name = "manager/platform/ingest_source_new.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("form", SourceForm())
        context.setdefault("report", None)
        return context

    @requires_level_method(GATE_ACTION)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = SourceForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        data = form.cleaned_data
        ctx = audit_context(request)
        create = request.POST.get("action") == "create"
        try:
            source, _changes = onboarding.create_source(
                data["vendor"],
                code=data["code"],
                adapter_code=data["adapter_code"],
                decimal=data["decimal"],
                ctx=ctx,
                dry_run=True,
            )
            report = onboarding.run_sample(source, data["sample"].read())
            if create:
                source, _changes = onboarding.create_source(
                    data["vendor"],
                    code=data["code"],
                    adapter_code=data["adapter_code"],
                    decimal=data["decimal"],
                    ctx=ctx,
                )
                onboarding.record_sample(source, report, ctx=ctx)
        except ValidationError as error:
            for name, problems in error.message_dict.items():
                form.add_error(name if name in form.fields else None, problems)
            return self.render_to_response(self.get_context_data(form=form), status=400)
        if create:
            messages.success(
                request, _("The source is created. It stays inactive until the checklist is met.")
            )
            return HttpResponseRedirect(
                reverse("manager:platform:ingest_source", args=[source.public_id])
            )
        return self.render_to_response(self.get_context_data(form=form, report=report))


class SourceChecklistView(ManagerView):
    """What is missing before this source can be activated."""

    section = SECTION
    template_name = "manager/platform/ingest_source.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        public_id: uuid.UUID = self.kwargs["public_id"]
        source = get_object_or_404(
            self.scoped(SupplierSource).select_related("vendor"), public_id=public_id
        )
        items = onboarding.checklist(source)
        context["source"] = source
        context["items"] = [
            {"key": item.key, "ok": item.ok, "label": CHECK_LABELS[item.key]} for item in items
        ]
        context["ready"] = all(i.ok for i in items if i.key != "source_active")
        context["list_url"] = reverse(LIST_URL)
        return context


CHECK_LABELS: Final = {
    "vendor_active": gettext_lazy("The vendor is active"),
    "adapter_known": gettext_lazy("The type of file is one we can read"),
    "mapping_complete": gettext_lazy("The columns for the SKU and the title are mapped"),
    "sample_clean": gettext_lazy("A sample was read without errors with the current mapping"),
    "source_active": gettext_lazy("The source is active"),
}
