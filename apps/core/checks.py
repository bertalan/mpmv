"""System checks registered in `CoreConfig.ready`.

- The TranslationModel reuse contract (I18N.2): `manage.py check` (and `make check`) fails when
  a `*Translation` table, e.g. a new catalog one, does not follow it (core.E101 to E104).
- The client IP settings (CORE.5): core.E001, core.E002 and the deploy warning core.W001.
- The audit policy (SEC.4): every model of our apps and every model in the staff admin is
  audited or exempt with a reason, and the admin of an audited model never writes through
  Django's default, unaudited methods, not even by calling them from an override
  (core.E201 to E203).
"""

import ast
import inspect
import textwrap
from collections.abc import Iterable, Mapping
from typing import Any

from django.apps import AppConfig, apps
from django.conf import settings
from django.contrib import admin
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.http import HttpRequest

from .http import trusted_proxy_networks
from .models import TranslationModel

# Workflow fields of the base model: never translated texts.
BASE_FIELDS = frozenset(f.name for f in TranslationModel._meta.get_fields())


def _field(model: type[models.Model], name: str) -> Any:
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _is_text(model: type[models.Model], name: str) -> bool:
    return name not in BASE_FIELDS and isinstance(
        _field(model, name), models.CharField | models.TextField
    )


def check_translation_model(model: type[TranslationModel]) -> list[checks.CheckMessage]:
    label = model._meta.label
    parent = model.translation_of
    texts = model.translated_fields
    constraints = model._meta.constraints
    errors: list[checks.CheckMessage] = []

    if not parent or not isinstance(_field(model, parent), models.ForeignKey):
        errors.append(
            checks.Error(
                f"{label}.translation_of must name a ForeignKey of the model (got {parent!r}).",
                obj=model,
                id="core.E101",
            )
        )

    if not texts or not all(_is_text(model, name) for name in texts):
        errors.append(
            checks.Error(
                f"{label}.translated_fields must list the model's own CharField or TextField "
                f"texts (got {texts!r}).",
                obj=model,
                id="core.E102",
            )
        )

    if not any(
        isinstance(c, models.UniqueConstraint)
        and set(c.fields) == {parent, "language"}
        and c.condition is None
        for c in constraints
    ):
        errors.append(
            checks.Error(
                f"{label} needs a UniqueConstraint on ({parent!r}, 'language'): one row per "
                "translated entity and language.",
                obj=model,
                id="core.E103",
            )
        )

    status_name = f"{model._meta.app_label}_{model._meta.model_name}_status_valid"
    if not any(
        isinstance(c, models.CheckConstraint) and c.name == status_name for c in constraints
    ):
        errors.append(
            checks.Error(
                f"{label} lost the status check constraint: declare "
                "`class Meta(TranslationModel.Meta)` and keep "
                "`*TranslationModel.Meta.constraints`.",
                obj=model,
                id="core.E104",
            )
        )
    return errors


def translation_models(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Check every concrete TranslationModel subclass of the given apps (default: all)."""
    if app_configs is None:
        candidates: Iterable[type[models.Model]] = apps.get_models()
    else:
        candidates = [model for config in app_configs for model in config.get_models()]
    errors: list[checks.CheckMessage] = []
    for model in candidates:
        if issubclass(model, TranslationModel):
            errors.extend(check_translation_model(model))
    return errors


def check_client_ip_settings(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """A trusted header needs the proxies it is trusted from, given as addresses or networks."""
    if not settings.TRUSTED_PROXY_HEADER:
        return []
    proxies = tuple(settings.TRUSTED_PROXIES)
    if not proxies:
        return [
            checks.Error(
                "TRUSTED_PROXY_HEADER is set but TRUSTED_PROXIES is empty.",
                hint="Set MPMV_TRUSTED_PROXIES to the reverse proxy's network (10.89.250.0/24).",
                id="core.E001",
            )
        ]
    try:
        trusted_proxy_networks(proxies)
    except ValueError as error:
        return [
            checks.Error(
                f"TRUSTED_PROXIES has an entry that is not an IP address or network: {error}.",
                hint="List addresses or CIDR networks, comma separated, never host names.",
                id="core.E002",
            )
        ]
    return []


def check_trusted_proxy_for_deploy(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Behind a reverse proxy, REMOTE_ADDR is the proxy: all clients share one IP throttle."""
    if settings.TRUSTED_PROXY_HEADER:
        return []
    return [
        checks.Warning(
            "TRUSTED_PROXY_HEADER is not set, so the client IP is REMOTE_ADDR.",
            hint=(
                "Behind a reverse proxy every client then shares the proxy's address, and the "
                "per-IP login lockout and registration limit apply to the whole site. Set "
                "MPMV_TRUSTED_PROXY_HEADER and MPMV_TRUSTED_PROXIES as in "
                "deploy/server/mpmv.env.example."
            ),
            id="core.W001",
        )
    ]


# --- Audit policy (SEC.4, docs/dev/audit-trail.md)
#
# Each AppConfig declares `audited_models` (names whose writes are audited) and
# `unaudited_models` ({name: reason}). Names are model names of the app, or full labels
# ("auth.group") for models of other packages that the app manages in the admin.

type AuditPolicy = Mapping[str, str | None]  # label -> None (audited) or the exemption reason
type AdminRegistry = Mapping[type[models.Model], admin.ModelAdmin[Any]]

LOCAL_APPS_PREFIX = "apps."
SAVE_METHODS = ("save_model",)
DELETE_METHODS = ("delete_model", "delete_queryset")


def _policy_label(config: AppConfig, name: str) -> str:
    return name.lower() if "." in name else f"{config.label}.{name.lower()}"


def audit_policy(configs: Iterable[AppConfig]) -> dict[str, str | None]:
    """The audit classification declared by the given apps."""
    policy: dict[str, str | None] = {}
    for config in configs:
        for name in getattr(config, "audited_models", ()):
            policy[_policy_label(config, name)] = None
        for name, reason in getattr(config, "unaudited_models", {}).items():
            policy[_policy_label(config, name)] = reason
    return policy


class _AnyPermission:
    """A user who holds every permission: what could an admin let someone write?"""

    is_active = is_staff = is_superuser = is_authenticated = True
    pk = None

    def has_perm(self, perm: str, obj: Any = None) -> bool:
        return True

    def has_perms(self, perms: Iterable[str], obj: Any = None) -> bool:
        return True

    def has_module_perms(self, app_label: str) -> bool:
        return True


def _request() -> HttpRequest:
    request = HttpRequest()
    request.user = _AnyPermission()  # type: ignore[assignment]
    return request


def _overrides(model_admin: Any, method: str) -> bool:
    return getattr(type(model_admin), method) is not getattr(admin.ModelAdmin, method)


def _calls_super(function: Any, method: str) -> bool | None:
    """Whether `function` calls `super().<method>(...)`; None when its source is unreadable."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except (OSError, TypeError, SyntaxError):
        return None
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "super"
        for node in ast.walk(tree)
    )


def _writes_directly(model_admin: Any, method: str) -> bool:
    """Whether `method` reaches Django's default, unaudited implementation: not overridden, or
    overridden by code that calls `super().<method>` down to Django's (coherence check 4)."""
    for cls in type(model_admin).__mro__:
        if method not in vars(cls):
            continue
        if cls.__module__.startswith("django."):
            return True
        if not _calls_super(vars(cls)[method], method):  # unreadable source: trust it
            return False
    return True


def _missing_writes(model_admin: admin.ModelAdmin[Any], request: HttpRequest) -> list[str]:
    """Write methods that reach Django's defaults. `save_model`, `delete_model` and
    `delete_queryset` must not call the default at all; `save_related` (many-to-many) may, as
    long as it is overridden to audit the change."""
    methods: list[str] = []
    if model_admin.has_add_permission(request) or model_admin.has_change_permission(request):
        methods += SAVE_METHODS
    if model_admin.has_delete_permission(request):
        methods += DELETE_METHODS
    missing = [method for method in methods if _writes_directly(model_admin, method)]
    if (
        (model_admin.has_add_permission(request) or model_admin.has_change_permission(request))
        and model_admin.model._meta.many_to_many
        and not _overrides(model_admin, "save_related")
    ):
        missing.append("save_related")
    return missing


def _inline_models(model_admin: admin.ModelAdmin[Any]) -> list[type[models.Model]]:
    return [inline.model for inline in model_admin.inlines]


def audit_policy_errors(
    local_models: Iterable[type[models.Model]],
    registry: AdminRegistry,
    policy: AuditPolicy,
) -> list[checks.CheckMessage]:
    """What breaks the audit policy among our models and the admin registrations."""
    errors: list[checks.CheckMessage] = []
    candidates = [
        *local_models,
        *registry,
        *(model for model_admin in registry.values() for model in _inline_models(model_admin)),
    ]
    for model in dict.fromkeys(candidates):
        label = model._meta.label_lower
        if label not in policy:
            errors.append(
                checks.Error(
                    f"{label} is not classified for the audit log.",
                    hint=(
                        "Add it to `audited_models` of its AppConfig (its writes go through "
                        "audited services) or to `unaudited_models` with the reason it is not "
                        "sensitive. See docs/dev/audit-trail.md."
                    ),
                    obj=model,
                    id="core.E201",
                )
            )
        elif policy[label] is not None and not str(policy[label]).strip():
            errors.append(
                checks.Error(
                    f"{label} is exempt from the audit log without a reason.",
                    obj=model,
                    id="core.E202",
                )
            )

    request = _request()
    for model, model_admin in registry.items():
        missing = (
            _missing_writes(model_admin, request)
            if policy.get(model._meta.label_lower, "") is None
            else []
        )
        audited_inlines = [
            m for m in _inline_models(model_admin) if policy.get(m._meta.label_lower, "") is None
        ]
        if audited_inlines and not _overrides(model_admin, "save_formset"):
            missing.append("save_formset")
        if missing:
            errors.append(
                checks.Error(
                    f"{type(model_admin).__qualname__} writes the audited "
                    f"{model._meta.label_lower} through Django's unaudited "
                    f"{', '.join(missing)}.",
                    hint=(
                        "Override them to call the app's audited services (without calling "
                        "the default through super()), or deny the permission (read-only "
                        "admin)."
                    ),
                    obj=type(model_admin),
                    id="core.E203",
                )
            )
    return errors


def check_audit_policy(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Every model of our apps and of the staff admin is classified; audited ones are audited."""
    configs = list(apps.get_app_configs())
    local = [
        model
        for config in configs
        if config.name.startswith(LOCAL_APPS_PREFIX)
        for model in config.get_models()
    ]
    return audit_policy_errors(local, admin.site._registry, audit_policy(configs))
