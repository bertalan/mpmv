"""SEC.4: the audit policy system check, so new models and admins cannot skip the audit log.

Every model of our apps (and every model in the staff admin) is either audited or exempt with a
reason. The admin of an audited model may not write through Django's default, unaudited
`save_model`, `save_related`, `save_formset`, `delete_model` or `delete_queryset`.
"""

from typing import Any

import pytest
from django.contrib import admin
from django.core import checks
from django.db import models
from django.db.models import QuerySet
from django.http import HttpRequest
from django.test.utils import isolate_apps

from apps.core.checks import audit_policy_errors
from apps.core.models import Heartbeat, PlatformOperator


def ids(messages: list[checks.CheckMessage]) -> list[str]:
    return [message.id or "" for message in messages]


class ReadOnlyAdmin(admin.ModelAdmin[Any]):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class AuditedWritesAdmin(admin.ModelAdmin[Any]):
    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        raise NotImplementedError

    def delete_model(self, request: HttpRequest, obj: Any) -> None:
        raise NotImplementedError

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        raise NotImplementedError


class SavesDirectlyAdmin(admin.ModelAdmin[Any]):
    """Overrides the writes but still saves through Django's default (coherence check 4)."""

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        super().save_model(request, obj, form, change)

    def delete_model(self, request: HttpRequest, obj: Any) -> None:
        raise NotImplementedError

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        super().delete_queryset(request, queryset)


class InheritsDirectSaveAdmin(SavesDirectlyAdmin):
    pass


class ExtendsAuditedWritesAdmin(AuditedWritesAdmin):
    """Calls super(), which reaches an audited override, not Django's default."""

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        super().save_model(request, obj, form, change)


class SavesOnlyAdmin(admin.ModelAdmin[Any]):
    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        raise NotImplementedError


def registry(model: type[models.Model], admin_class: type[admin.ModelAdmin[Any]]) -> Any:
    return {model: admin_class(model, admin.site)}


def test_every_model_of_our_apps_must_be_classified() -> None:
    errors = audit_policy_errors([Heartbeat], {}, {})
    assert ids(errors) == ["core.E201"]
    assert "core.heartbeat" in errors[0].msg


def test_an_exemption_needs_a_reason() -> None:
    assert ids(audit_policy_errors([Heartbeat], {}, {"core.heartbeat": " "})) == ["core.E202"]


def test_classified_models_pass() -> None:
    policy = {"core.heartbeat": "operational telemetry", "core.platformoperator": None}
    assert audit_policy_errors([Heartbeat, PlatformOperator], {}, policy) == []


def test_a_model_in_the_admin_must_be_classified_even_from_another_package() -> None:
    errors = audit_policy_errors([], registry(Heartbeat, admin.ModelAdmin), {})
    assert ids(errors) == ["core.E201"]


def test_the_admin_of_an_audited_model_cannot_use_the_default_writes() -> None:
    policy = {"core.platformoperator": None}
    errors = audit_policy_errors([], registry(PlatformOperator, admin.ModelAdmin), policy)
    assert ids(errors) == ["core.E203"]
    for method in ("save_model", "delete_model", "delete_queryset"):
        assert method in errors[0].msg


def test_a_deletable_audited_model_must_audit_deletions_too() -> None:
    policy = {"core.platformoperator": None}
    errors = audit_policy_errors([], registry(PlatformOperator, SavesOnlyAdmin), policy)
    assert ids(errors) == ["core.E203"]
    assert "save_model" not in errors[0].msg
    assert "delete_model" in errors[0].msg


def test_a_read_only_admin_of_an_audited_model_passes() -> None:
    policy = {"core.platformoperator": None}
    assert audit_policy_errors([], registry(PlatformOperator, ReadOnlyAdmin), policy) == []


def test_an_admin_that_overrides_every_write_passes() -> None:
    policy = {"core.platformoperator": None}
    assert audit_policy_errors([], registry(PlatformOperator, AuditedWritesAdmin), policy) == []


@pytest.mark.parametrize("admin_class", [SavesDirectlyAdmin, InheritsDirectSaveAdmin])
def test_an_override_that_calls_the_default_write_is_still_unaudited(
    admin_class: type[admin.ModelAdmin[Any]],
) -> None:
    policy = {"core.platformoperator": None}
    errors = audit_policy_errors([], registry(PlatformOperator, admin_class), policy)
    assert ids(errors) == ["core.E203"]
    assert "save_model" in errors[0].msg
    assert "delete_queryset" in errors[0].msg
    assert "delete_model" not in errors[0].msg


def test_an_override_may_call_an_audited_parent_override() -> None:
    policy = {"core.platformoperator": None}
    registered = registry(PlatformOperator, ExtendsAuditedWritesAdmin)
    assert audit_policy_errors([], registered, policy) == []


def test_the_admin_of_an_exempt_model_is_not_checked() -> None:
    policy = {"core.heartbeat": "operational telemetry"}
    assert audit_policy_errors([], registry(Heartbeat, admin.ModelAdmin), policy) == []


@isolate_apps("apps.core")
def test_many_to_many_fields_of_an_audited_model_need_an_audited_save_related() -> None:
    class Tag(models.Model):  # noqa: DJ008
        class Meta:
            app_label = "core"

    class Tagged(models.Model):  # noqa: DJ008
        tags = models.ManyToManyField(Tag)

        class Meta:
            app_label = "core"

    errors = audit_policy_errors([], registry(Tagged, AuditedWritesAdmin), {"core.tagged": None})
    assert ids(errors) == ["core.E203"]
    assert "save_related" in errors[0].msg


@isolate_apps("apps.core")
def test_an_inline_of_an_audited_model_needs_an_audited_save_formset() -> None:
    class Parent(models.Model):  # noqa: DJ008
        class Meta:
            app_label = "core"

    class Child(models.Model):  # noqa: DJ008
        parent = models.ForeignKey(Parent, on_delete=models.CASCADE)

        class Meta:
            app_label = "core"

    class ChildInline(admin.TabularInline[Child, Parent]):
        model = Child

    class ParentAdmin(AuditedWritesAdmin):
        inlines = (ChildInline,)

    policy = {"core.parent": "test parent", "core.child": None}
    errors = audit_policy_errors([], registry(Parent, ParentAdmin), policy)
    assert ids(errors) == ["core.E203"]
    assert "save_formset" in errors[0].msg
