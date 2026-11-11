"""System checks registered in `CoreConfig.ready`.

- The TranslationModel reuse contract (I18N.2): `manage.py check` (and `make check`) fails when
  a `*Translation` table, e.g. a new catalog one, does not follow it (core.E101 to E104).
- The client IP settings (CORE.5): core.E001, core.E002 and the deploy warning core.W001.
- The audit policy (SEC.4): every model of our apps and every model in the staff admin is
  audited or exempt with a reason, and the admin of an audited model never writes through
  Django's default, unaudited methods, not even by calling them from an override
  (core.E201 to E203).
- Field-level encryption (SEC.3): a valid key is configured (core.E301, E302) and, when a
  database is checked (`migrate`, `check --database default`), every stored value decrypts with
  the configured keys (core.E303, E304); values in clear or on an older key are warnings
  (core.W302, W301) that `manage.py rotate_field_keys` fixes.
- Feature flags (ARC.8): every environment default names a registered flag (core.E401).
- Transactional email (ARC.16): settings that hold live data (`LIVE_DATA`) send through a real
  provider adapter, not the console or test sender (core.E501), from a real address
  (core.E502); without live data (development, tests, STAGE) the console sender stays allowed
  until ARC.19.
- Image formats (THM.12): a warning when Pillow cannot encode AVIF (core.W501).
"""

import ast
import inspect
import math
import textwrap
from collections.abc import Iterable, Mapping
from typing import Any

from django.apps import AppConfig, apps
from django.conf import settings
from django.contrib import admin
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import DatabaseError, connections, models, transaction
from django.http import HttpRequest

from . import encryption, flags
from .crypto import DecryptionError
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


def check_admin_network_settings(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """OPS.5: every allowed network of the technical admin is an address or a CIDR network."""
    try:
        trusted_proxy_networks(tuple(settings.ADMIN_ALLOWED_NETWORKS))
    except ValueError as error:
        return [
            checks.Error(
                f"ADMIN_ALLOWED_NETWORKS has an entry that is not an IP address or network: "
                f"{error}.",
                hint="List addresses or CIDR networks, comma separated, never host names.",
                id="core.E003",
            )
        ]
    return []


def check_admin_network_for_deploy(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """OPS.5: with no allowed network the Django admin answers everybody (only Caddy limits it)."""
    if settings.ADMIN_ALLOWED_NETWORKS:
        return []
    return [
        checks.Warning(
            "ADMIN_ALLOWED_NETWORKS is empty: the app does not restrict the Django admin.",
            hint="Set MPMV_ADMIN_ALLOWED_NETWORKS as in deploy/server/mpmv.env.example.",
            id="core.W002",
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


# --- Field-level encryption (SEC.3)


def check_field_encryption_keys(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """A usable key must be configured: without one, encrypted fields can be neither written
    nor read. Messages never show key material."""
    if not getattr(settings, encryption.KEYS_SETTING, None):
        return [
            checks.Error(
                "No field encryption key is configured.",
                hint=encryption.KEYS_HELP,
                id="core.E301",
            )
        ]
    try:
        encryption.configured_keyring()
    except ImproperlyConfigured as exc:
        return [checks.Error(str(exc), id="core.E302")]
    return []


def check_encrypted_data(
    app_configs: Iterable[AppConfig] | None,
    databases: Iterable[str] | None = None,
    **kwargs: Any,
) -> list[checks.CheckMessage]:
    """Every encrypted column decrypts with the configured keys (one sample per key id).

    Runs only when a database is checked: `migrate` does it before migrating, so a deploy with
    a wrong or retired key stops there. Tables not created yet are skipped. One sample per key
    keeps the deploy fast but cannot see a single bad row: `manage.py verify_encrypted_data`
    decrypts them all.
    """
    if not databases:
        return []
    try:
        keyring = encryption.configured_keyring()
    except ImproperlyConfigured:
        return []  # reported by check_field_encryption_keys
    messages: list[checks.CheckMessage] = []
    for alias in databases:
        connection = connections[alias]
        for column in encryption.encrypted_columns():
            try:
                with transaction.atomic(using=alias):
                    usage = encryption.key_usage(
                        connection, column.model._meta.db_table, column.column
                    )
            except DatabaseError:
                continue  # not migrated yet
            messages += _column_messages(column, usage, keyring)
    return messages


def _column_messages(
    column: encryption.EncryptedColumn, usage: Mapping[str, list[Any]], keyring: Any
) -> list[checks.CheckMessage]:
    label = column.context
    rotate = "Run `manage.py rotate_field_keys`."
    messages: list[checks.CheckMessage] = []
    for key_id, (count, sample) in usage.items():
        if key_id == encryption.CLEARTEXT:
            messages.append(
                checks.Warning(
                    f"{count} value(s) of {label} are stored in clear.", hint=rotate, id="core.W302"
                )
            )
        elif key_id not in keyring.key_ids:
            messages.append(
                checks.Error(
                    f"{count} value(s) of {label} use key {key_id!r}, which is not configured.",
                    hint="Restore that key (after the primary one) until the values are rotated.",
                    id="core.E303",
                )
            )
        else:
            try:
                keyring.decrypt(sample, context=label)
            except DecryptionError:
                messages.append(
                    checks.Error(
                        f"Key {key_id!r} does not decrypt {label}: it is not the key the data "
                        "was encrypted with.",
                        hint=encryption.KEYS_HELP,
                        id="core.E304",
                    )
                )
                continue
            if key_id != keyring.primary_id:
                messages.append(
                    checks.Warning(
                        f"{count} value(s) of {label} still use the older key {key_id!r}.",
                        hint=rotate,
                        id="core.W301",
                    )
                )
    return messages


# --- Feature flags (ARC.8, docs/dev/feature-flags.md)


def check_feature_flag_settings(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Environment defaults name registered flags: a typo must not leave a kill switch as is."""
    unknown = sorted(name for name in settings.FEATURE_FLAGS if not flags.is_registered(name))
    if not unknown:
        return []
    known = ", ".join(spec.name for spec in flags.registered_flags())
    return [
        checks.Error(
            f"FEATURE_FLAGS names flags that are not registered: {', '.join(unknown)}.",
            hint=f"Fix MPMV_FEATURE_FLAGS; registered flags: {known}.",
            id="core.E401",
        )
    ]


# Senders that never deliver: development and tests only.
NON_DELIVERING_SENDERS = frozenset(
    {"apps.core.mail.ConsoleEmailSender", "apps.core.mail.MemoryEmailSender"}
)


def check_email_settings(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Where live data is held, a forgotten email variable must stop the start, not drop mail."""
    if not getattr(settings, "LIVE_DATA", False):
        return []
    errors: list[checks.CheckMessage] = []
    sender = (settings.EMAIL_SENDER or "").strip()
    if not sender or sender in NON_DELIVERING_SENDERS:
        errors.append(
            checks.Error(
                f"EMAIL_SENDER is {sender or 'empty'!r}: transactional email would not be sent.",
                hint="Set MPMV_EMAIL_SENDER to the provider adapter (ARC.19).",
                id="core.E501",
            )
        )
    address = (settings.DEFAULT_FROM_EMAIL or "").strip().lower()
    if not address or address.endswith("@localhost"):
        errors.append(
            checks.Error(
                f"DEFAULT_FROM_EMAIL is {address or 'empty'!r}: not a real sender address.",
                hint="Set MPMV_EMAIL_FROM to an address of the platform's sending domain.",
                id="core.E502",
            )
        )
    return errors


# --- Image formats (THM.12, spec 14 §14.7)


def check_avif_support(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Without an AVIF encoder, pictures fall back to WebP and JPEG: say so (core.W501)."""
    from . import images

    if images.avif_supported():
        return []
    return [
        checks.Warning(
            "Pillow cannot encode AVIF here: <picture> elements offer WebP and JPEG only.",
            hint=(
                "Install the Pillow wheel from uv.lock (it bundles libavif), or a Pillow built "
                "against libavif."
            ),
            id="core.W501",
        )
    ]


# --- Upload limits (SEC.2)

UPLOAD_LIMIT_SETTINGS = (
    "WAGTAILIMAGES_MAX_UPLOAD_SIZE",
    "WAGTAILIMAGES_MAX_IMAGE_PIXELS",
    "MPMV_MAX_ANIMATION_PIXELS",
    "MPMV_MAX_STORED_IMAGE_PIXELS",
    "MPMV_MAX_IMAGE_SIDE",
    "MPMV_MAX_IMAGE_FRAMES",
    "MPMV_IMAGE_DECODE_WAIT_SECONDS",
)


def check_image_upload_limits(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Every upload cap is a finite positive number, the decode wait at most
    MAX_DECODE_WAIT_SECONDS (core.E601), and the stored and animation caps fit within the
    still-image decode cap (core.E602). A value that is not a number at all stops the settings
    import (ImproperlyConfigured, mpmv.settings.env), before any check runs."""
    from .uploads import MAX_DECODE_WAIT_SECONDS

    values = {name: getattr(settings, name, None) for name in UPLOAD_LIMIT_SETTINGS}
    wrong = [
        name
        for name, value in values.items()
        if isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ]
    wait = values["MPMV_IMAGE_DECODE_WAIT_SECONDS"]
    if "MPMV_IMAGE_DECODE_WAIT_SECONDS" not in wrong and wait > MAX_DECODE_WAIT_SECONDS:  # type: ignore[operator]
        wrong.append("MPMV_IMAGE_DECODE_WAIT_SECONDS")
    if wrong:
        return [
            checks.Error(
                f"Upload limits must be finite positive numbers: {', '.join(wrong)}"
                f" (MPMV_IMAGE_DECODE_WAIT_SECONDS at most {MAX_DECODE_WAIT_SECONDS:g}).",
                hint="Fix the MPMV_MAX_* variables in the environment (mpmv.env).",
                id="core.E601",
            )
        ]
    decode = values["WAGTAILIMAGES_MAX_IMAGE_PIXELS"]
    above = [
        name
        for name in ("MPMV_MAX_STORED_IMAGE_PIXELS", "MPMV_MAX_ANIMATION_PIXELS")
        if values[name] > decode  # type: ignore[operator]
    ]
    if above:
        return [
            checks.Error(
                f"{', '.join(above)} must not exceed WAGTAILIMAGES_MAX_IMAGE_PIXELS ({decode}).",
                hint="Lower them, or raise MPMV_MAX_IMAGE_PIXELS.",
                id="core.E602",
            )
        ]
    return []


def check_public_forms(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """The anonymous POST form sweep (SEC.13, apps/core/public_forms.py)."""
    from .public_forms import public_form_report

    report = public_form_report()
    messages: list[checks.CheckMessage] = [
        checks.Error(message, hint="See docs/dev/public-forms.md.", id=check_id)
        for check_id, message in report.errors
    ]
    messages += [checks.Info(message, id=check_id) for check_id, message in report.warnings]
    return messages
