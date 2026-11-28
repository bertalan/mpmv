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
from django.db import transaction
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
                with transaction.atomic():  # the source and its sample result, or neither
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


class _SourceView(WriteGate, ManagerView):
    section = SECTION
    write_section = SECTION
    refuse_read_only = True

    def source(self) -> SupplierSource:
        public_id: uuid.UUID = self.kwargs["public_id"]
        return get_object_or_404(
            self.scoped(SupplierSource).select_related("vendor"), public_id=public_id
        )

    def back(self) -> HttpResponseRedirect:
        return HttpResponseRedirect(
            reverse("manager:platform:ingest_source", args=[self.kwargs["public_id"]])
        )


class SampleForm(forms.Form):
    sample = forms.FileField(label=gettext_lazy("Sample file"))

    def clean_sample(self) -> Any:
        return SourceForm.clean_sample(self)  # type: ignore[arg-type]


class SourceChecklistView(_SourceView):
    """What is missing before this source can be activated; re-check the sample and activate."""

    template_name = "manager/platform/ingest_source.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        source = self.source()
        items = onboarding.checklist(source)
        context["source"] = source
        context["items"] = [{"key": item.key, "ok": item.ok, "label": item.label} for item in items]
        context["ready"] = all(i.ok for i in items if i.key != "source_active")
        context["can_write"] = writer(SECTION)(self.manager)
        context.setdefault("form", SampleForm())
        context["list_url"] = reverse(LIST_URL)
        return context

    @requires_level_method(GATE_ACTION)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        source = self.source()
        ctx = audit_context(request)
        if request.POST.get("action") == "activate":
            try:
                onboarding.activate_source(source, ctx=ctx)
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
                return self.render_to_response(self.get_context_data(), status=400)
            messages.success(
                request, _("The source is active. Runs stay dry until writing is enabled.")
            )
            return self.back()
        form = SampleForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        report = onboarding.run_sample(source, form.cleaned_data["sample"].read())
        onboarding.record_sample(source, report, ctx=ctx)
        messages.info(request, _("The sample was read again with the current mapping."))
        return self.back()


class SourceMappingView(_SourceView):
    """Step 2: map the columns of a sample to fields. The sample only gives the column names; it
    is not kept. The pairs are posted back and saved through the audited mapping service."""

    template_name = "manager/platform/ingest_source_mapping.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["source"] = self.source()
        context["can_write"] = writer(SECTION)(self.manager)
        context.setdefault("form", SampleForm())
        context.setdefault("columns", None)
        context.setdefault("failure", "")
        context["fields"] = base.FIELDS
        return context

    @requires_level_method(GATE_ACTION)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        source = self.source()
        if request.POST.get("action") == "save":
            pairs: dict[str, str] = {}
            for i in range(onboarding.MAX_HEADERS):
                header = request.POST.get(f"header_{i}")
                if header is None:
                    break
                name = request.POST.get(f"field_{i}", "")
                if name:
                    pairs[header[:200]] = name
            try:
                onboarding.save_column_mappings(source.vendor, pairs, ctx=audit_context(request))
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
                return self.render_to_response(self.get_context_data(), status=400)
            messages.success(
                request, _("The mapping is saved. Read the sample again to confirm it.")
            )
            return self.back()
        form = SampleForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        report = onboarding.run_sample(source, form.cleaned_data["sample"].read())
        if not report.headers:
            return self.render_to_response(
                self.get_context_data(failure=report.failure or _("The file has no columns.")),
                status=400,
            )
        current = {
            e.key.casefold(): e.target_value for e in onboarding.column_entries(source.vendor)
        }
        columns = [
            {
                "header": h,
                "field": current.get(
                    h.casefold(), h.casefold() if h.casefold() in base.FIELDS else ""
                ),
            }
            for h in report.headers
        ]
        return self.render_to_response(self.get_context_data(columns=columns))
