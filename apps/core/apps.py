from django.apps import AppConfig
from django.core import checks


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    # Audit policy (SEC.4, docs/dev/audit-trail.md), enforced by the core.E2xx system check.
    audited_models = ("platformoperator",)
    unaudited_models = {
        "auditlog": "the audit log itself (append-only)",
        "heartbeat": "worker telemetry, not a domain change",
        "throttle": "login counters; the lockouts they cause are audited by accounts",
        # The job queue (ADR-0004) is used only through apps.core.tasks; its admin is read-only.
        "procrastinate.procrastinatejob": "background job queue, read-only admin",
        "procrastinate.procrastinateevent": "background job history, read-only admin",
    }

    def ready(self) -> None:
        from .checks import (
            check_audit_policy,
            check_client_ip_settings,
            check_encrypted_data,
            check_field_encryption_keys,
            check_trusted_proxy_for_deploy,
            translation_models,
        )

        checks.register(translation_models)
        checks.register(check_audit_policy, checks.Tags.security)
        checks.register(check_client_ip_settings, checks.Tags.security)
        checks.register(check_trusted_proxy_for_deploy, checks.Tags.security, deploy=True)
        checks.register(check_field_encryption_keys, checks.Tags.security)
        checks.register(check_encrypted_data, checks.Tags.database)
