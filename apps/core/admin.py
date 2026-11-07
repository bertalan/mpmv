"""Staff admin for the platform operator, the audit log and the feature flags (Wagtail admin
replaces this in CORE.8).

Configuration rows are history: staff add a new row (closing the current one through the audited
service) and can view earlier rows, but never edit or delete them.

The audit log view (SEC.4) is read-only and filterable by actor, action, object type, date range
and vendor. It asks for a fresh second factor through `settings.STEP_UP_DECORATOR` (the CORE.4
`step_up_required`, named by dotted path because core must not import accounts).
"""

import datetime as dt
from collections.abc import Callable, Iterator
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.views.main import ChangeList
from django.db.models import Model, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.module_loading import import_string

from . import flags
from .audit import audit_context
from .changeset import diff
from .models import AuditLog, FeatureFlag, PlatformOperator
from .services import (
    check_operator_start,
    configure_platform_operator,
    environment_default,
    latest_operator,
    save_flag,
)


class PlatformOperatorForm(forms.ModelForm[PlatformOperator]):
    class Meta:
        model = PlatformOperator
        fields = (
            "legal_name",
            "country",
            "vat_number",
            "tax_regime",
            "commission_vat_mode",
            "einvoice_channel",
            "dac7_authority_country",
            "valid_from",
        )

    def clean_valid_from(self) -> dt.datetime:
        valid_from: dt.datetime = self.cleaned_data["valid_from"]
        if self.instance.pk is None:
            try:
                check_operator_start(valid_from, latest_operator())
            except forms.ValidationError as exc:
                raise forms.ValidationError(exc.message_dict["valid_from"]) from exc
        return valid_from


@admin.register(PlatformOperator)
class PlatformOperatorAdmin(admin.ModelAdmin[PlatformOperator]):
    form = PlatformOperatorForm
    list_display = (
        "legal_name",
        "country",
        "vat_number",
        "commission_vat_mode",
        "einvoice_channel",
        "valid_from",
        "valid_to",
    )
    readonly_fields = ("public_id", "valid_to", "created_at")

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(
        self, request: HttpRequest, obj: PlatformOperator, form: Any, change: bool
    ) -> None:
        configure_platform_operator(obj, ctx=audit_context(request))


# --- Audit log (SEC.4)


class InputFilter(admin.ListFilter):
    """A sidebar filter with free-text (or date) inputs instead of a list of links."""

    template = "admin/core/input_filter.html"
    inputs: tuple[tuple[str, str, str], ...] = ()  # (query parameter, label, input type)

    def __init__(
        self,
        request: HttpRequest,
        params: dict[str, Any],
        model: type[Model],
        model_admin: admin.ModelAdmin[Any],
    ) -> None:
        super().__init__(request, params, model, model_admin)
        for name in self.expected_parameters():
            if name in params:
                value = params.pop(name)
                self.used_parameters[name] = value[-1] if isinstance(value, list) else value

    def expected_parameters(self) -> list[str | None]:
        return [name for name, _, _ in self.inputs]

    def has_output(self) -> bool:
        return True

    def values(self) -> dict[str, str]:
        return {k: str(v).strip() for k, v in self.used_parameters.items() if str(v).strip()}

    def choices(self, changelist: ChangeList) -> Iterator[dict[str, Any]]:
        own = set(self.expected_parameters())
        preserved = [
            (name, value[-1] if isinstance(value, list) else value)
            for name, value in changelist.params.items()
            if name not in own and name not in {"p", "e"}
        ]
        values = self.values()
        yield {
            "preserved": preserved,
            "inputs": [
                (name, label, kind, values.get(name, "")) for name, label, kind in self.inputs
            ],
        }

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        values = self.values()
        return self.filter(queryset, values) if values else queryset

    def filter(self, queryset: QuerySet[Any], values: dict[str, str]) -> QuerySet[Any]:
        raise NotImplementedError


class ActorFilter(InputFilter):
    title = "actor"
    inputs = (("actor", "Actor label contains", "text"),)

    def filter(self, queryset: QuerySet[Any], values: dict[str, str]) -> QuerySet[Any]:
        return queryset.filter(actor_label__icontains=values["actor"])


class VendorFilter(InputFilter):
    title = "vendor"
    inputs = (("vendor", "Vendor public id", "text"),)

    def filter(self, queryset: QuerySet[Any], values: dict[str, str]) -> QuerySet[Any]:
        return queryset.filter(vendor_key=values["vendor"])


class DateRangeFilter(InputFilter):
    """Entries created between two dates (inclusive), in the current time zone."""

    title = "date range"
    inputs = (("created_from", "From", "date"), ("created_to", "To", "date"))

    def filter(self, queryset: QuerySet[Any], values: dict[str, str]) -> QuerySet[Any]:
        try:
            start = values.get("created_from")
            end = values.get("created_to")
            condition = Q()
            if start:
                condition &= Q(created_at__date__gte=dt.date.fromisoformat(start))
            if end:
                condition &= Q(created_at__date__lte=dt.date.fromisoformat(end))
        except ValueError as exc:  # the admin answers with its "wrong filter" page
            raise IncorrectLookupParameters(exc) from exc
        return queryset.filter(condition)


type AdminView = Callable[[HttpRequest], HttpResponse]


def step_up(view: AdminView) -> AdminView:
    """Wrap `view` with the step-up decorator named in settings, if any."""
    path = getattr(settings, "STEP_UP_DECORATOR", "")
    if not path:
        return view
    decorator: Callable[[], Callable[[AdminView], AdminView]] = import_string(path)
    return decorator()(view)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin[AuditLog]):
    """Read-only: the audit log is append-only, here and in the database."""

    change_form_template = "admin/core/auditlog/change_form.html"
    list_display = (
        "created_at",
        "actor_label",
        "source",
        "action",
        "target_model",
        "target_key",
        "vendor_key",
        "ip",
    )
    list_filter = (
        ActorFilter,
        "action",
        "target_model",
        "source",
        DateRangeFilter,
        VendorFilter,
    )
    search_fields = ("target_key", "actor_label", "action")
    date_hierarchy = "created_at"
    actions = ()
    readonly_fields = (
        "public_id",
        "created_at",
        "actor_id",
        "actor_label",
        "source",
        "action",
        "target_model",
        "target_key",
        "vendor_key",
        "before",
        "after",
        "ip",
        "request_id",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().changelist_view
        return step_up(lambda req: parent(req, extra_context))(request)

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        parent = super().change_view

        def view(req: HttpRequest) -> HttpResponse:
            entry = self.get_object(req, object_id)
            changes = [] if entry is None else changed_fields(entry)
            return parent(
                req, object_id, form_url, {**(extra_context or {}), "audit_changes": changes}
            )

        return step_up(view)(request)

    def history_view(
        self, request: HttpRequest, object_id: str, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse | TemplateResponse:
        parent = super().history_view
        return step_up(lambda req: parent(req, object_id, extra_context))(request)


# --- Feature flags and kill switches (ARC.8, docs/dev/feature-flags.md)


def _on_off(value: bool) -> str:
    return "on" if value else "off"


class FeatureFlagForm(forms.ModelForm[FeatureFlag]):
    """On a new row, the name is picked among the registered flags that have no row yet."""

    class Meta:
        model = FeatureFlag
        fields = ("name", "state", "note")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if "name" in self.fields:
            taken = set(FeatureFlag.objects.values_list("name", flat=True))
            choices = [
                (spec.name, f"{spec.name}: {spec.description}")
                for spec in flags.registered_flags()
                if spec.name not in taken
            ]
            self.fields["name"] = forms.ChoiceField(choices=choices)


def _is_kill_switch(flag: FeatureFlag | None) -> bool:
    spec = None if flag is None else flag.spec
    return spec is not None and spec.kill_switch


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin[FeatureFlag]):
    """Staff switches of the registered flags. Kill switches ask for a fresh second factor
    (CORE.4 step-up), and so does the add page, where the flag is chosen. Rows are never
    deleted: the state goes back to "Environment default" instead. Per-vendor overrides are
    under Vendors → Vendor feature flags."""

    form = FeatureFlagForm
    list_display = (
        "name",
        "description",
        "kill_switch",
        "state",
        "environment_default",
        "effective",
        "note",
        "updated_at",
    )
    list_filter = ("state",)
    search_fields = ("name", "note")
    readonly_fields = (
        "description",
        "kill_switch",
        "environment_default",
        "effective",
        "public_id",
        "updated_at",
    )

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        fields = tuple(self.readonly_fields)
        return ("name", *fields) if obj is not None else fields

    def get_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        if obj is None:
            return ("name", "state", "note")
        return ("name", "description", "kill_switch", "state", "note", *self.readonly_fields[2:])

    @admin.display(description="Description")
    def description(self, obj: FeatureFlag) -> str:
        spec = obj.spec
        return "Not a registered flag (left by removed code)" if spec is None else spec.description

    @admin.display(description="Kill switch", boolean=True)
    def kill_switch(self, obj: FeatureFlag) -> bool:
        return _is_kill_switch(obj)

    @admin.display(description="Environment default")
    def environment_default(self, obj: FeatureFlag) -> str:
        spec = obj.spec
        if spec is None:
            return "-"
        value = environment_default(obj.name)
        return _on_off(spec.default if value is None else value)

    @admin.display(description="Effective (platform)")
    def effective(self, obj: FeatureFlag) -> str:
        """The value for the platform; vendor overrides may differ for their vendor."""
        spec = obj.spec
        if spec is None:
            return "-"
        value = flags.platform_value(
            spec, environment=environment_default(obj.name), state=obj.state
        )
        return _on_off(value)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(self, request: HttpRequest, obj: FeatureFlag, form: Any, change: bool) -> None:
        save_flag(obj, ctx=audit_context(request))

    def add_view(
        self, request: HttpRequest, form_url: str = "", extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().add_view
        return step_up(lambda req: parent(req, form_url, extra_context))(request)

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        parent = super().change_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, form_url, extra_context)

        if _is_kill_switch(self.get_object(request, object_id)):
            return step_up(view)(request)
        return view(request)


def changed_fields(entry: AuditLog) -> list[tuple[str, Any, Any]]:
    """The fields an entry changed, as (field, before, after) rows for the detail page."""
    before = entry.before if isinstance(entry.before, dict) else {}
    after = entry.after if isinstance(entry.after, dict) else {}
    return [(name, old, new) for name, (old, new) in diff(before, after).items()]
