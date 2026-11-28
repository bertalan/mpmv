from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "apps.orders"
    label = "orders"

    # Audit policy (SEC.4, docs/dev/audit-trail.md): orders are written only through
    # `apps.orders.services`, which audits every change (without addresses or e-mail). An
    # amendment and the versions it writes go through `apps.orders.amendments` (ORD.20); v1 is
    # the placed order, audited with it by `create_order`. A staff draft (ORD.32) and its lines
    # are written only through `apps.orders.staff_drafts`, which audits each step with the role
    # and the note.
    audited_models = (
        "order",
        "vendororder",
        "orderline",
        "vendororderversion",
        "orderamendment",
        "staffdraft",
        "staffdraftline",
    )
    unaudited_models = {
        "ordernumbersequence": "a counter row locked while a number is taken; the number is "
        "audited with the order it belongs to",
        "stockreservation": "a short-lived technical hold on stock; the order that confirms it "
        "is audited, and the stock change is audited when the payment succeeds",
        "orderevent": "an append-only copy of a notice sent (durable medium, ORD.20); it is "
        "written with the audited change it reports, which names it",
    }

    def ready(self) -> None:
        # The confirmation PDF is built when the email is sent (ORD.9 3/3); `core` only knows
        # the kind. Importing `documents` does not load WeasyPrint (it is imported on first use).
        from django.utils.translation import gettext

        from apps.core.services import register_email_attachment

        from . import amendment_engine, documents

        # The amendment engine is pure (ARC.18): it receives the translation function here.
        amendment_engine.install_translation(gettext)

        register_email_attachment(
            documents.ATTACHMENT_KIND, documents.vendor_confirmation_attachment
        )
        from django.core import checks

        from apps.core.services import register_business_zone_guard

        from .checks import check_trigger_time_zone
        from .tz import trigger_zone_refusal

        # ARC.27 until ORD.41: fail closed on a zone the order triggers cannot follow.
        checks.register(check_trigger_time_zone, checks.Tags.database)
        register_business_zone_guard(trigger_zone_refusal)
