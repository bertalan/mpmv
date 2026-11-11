from django.apps import AppConfig
from django.core import checks


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    # Audit policy (SEC.4, docs/dev/audit-trail.md), enforced by the core.E2xx system check.
    audited_models = ("platformoperator", "featureflag", "emailsuppression", "imagelicence")
    unaudited_models = {
        "auditlog": "the audit log itself (append-only)",
        "heartbeat": "worker telemetry, not a domain change",
        "throttle": "login counters; the lockouts they cause are audited by accounts",
        "geocodecacheentry": "cache of public geocoding answers, not a domain change",
        "geocoderstate": "provider rate limiter and circuit breaker, operational state",
        "mediaimage": "Wagtail image: editor upload, logged in Wagtail's action log; its licence "
        "metadata is validated by the model (CMS.8)",
        "mediaimagerendition": "a resized copy of an image, made by templates",
        "svgcollection": "which collection accepts SVG logos; written by a data migration only, "
        "not editable in the admin",
        "outboxemail": "delivery queue of transactional email; its history is outboxemailevent",
        "outboxemailevent": "append-only delivery history of the outbox",
        # The job queue (ADR-0004) is used only through apps.core.tasks; its admin is read-only.
        "procrastinate.procrastinatejob": "background job queue, read-only admin",
        "procrastinate.procrastinateevent": "background job history, read-only admin",
    }

    def ready(self) -> None:
        from django.conf import settings

        from .checks import (
            check_admin_network_for_deploy,
            check_admin_network_settings,
            check_audit_policy,
            check_avif_support,
            check_client_ip_settings,
            check_email_settings,
            check_encrypted_data,
            check_feature_flag_settings,
            check_field_encryption_keys,
            check_image_upload_limits,
            check_public_forms,
            check_trusted_proxy_for_deploy,
            translation_models,
        )

        checks.register(translation_models)
        checks.register(check_audit_policy, checks.Tags.security)
        checks.register(check_public_forms, checks.Tags.security, checks.Tags.urls)
        checks.register(check_client_ip_settings, checks.Tags.security)
        checks.register(check_trusted_proxy_for_deploy, checks.Tags.security, deploy=True)
        checks.register(check_admin_network_settings, checks.Tags.security)
        checks.register(check_admin_network_for_deploy, checks.Tags.security, deploy=True)
        checks.register(check_field_encryption_keys, checks.Tags.security)
        checks.register(check_encrypted_data, checks.Tags.database)
        checks.register(check_feature_flag_settings)
        checks.register(check_email_settings)
        checks.register(check_avif_support)
        checks.register(check_image_upload_limits)

        # Pillow's decompression-bomb threshold is process-wide: set once here, to the upload
        # still-image decode cap (SEC.2), so every Pillow use of the process (uploads,
        # renditions, imports) sees the same value whatever ran before. Uploads above the cap
        # are refused by apps.core.uploads itself.
        from .uploads import configure_pillow

        configure_pillow(settings.WAGTAILIMAGES_MAX_IMAGE_PIXELS)
