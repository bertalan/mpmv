from django.apps import AppConfig


class FitmentConfig(AppConfig):
    name = "apps.fitment"
    label = "fitment"
    default_auto_field = "django.db.models.BigAutoField"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). Every write goes through
    # `apps.fitment.services`, which writes one AuditLog entry per change.
    def ready(self) -> None:
        # What points at a node is registered here, not as an import side effect. FIT.2 and FIT.5
        # call `register_node_reference` from their own `ready()` (see references.py).
        from .references import register_builtin_references

        register_builtin_references()

        from django.core import checks

        from .checks import check_garage_cookie_settings

        checks.register(check_garage_cookie_settings)

        # FIT.18: the catalog cannot import fitment, so the publish gate registers itself here.
        from apps.catalog.publishing import register_publication_check

        from .services import publish_gate_messages

        register_publication_check("fitment", publish_gate_messages)

        # FIT.6: at sign-in the anonymous garage is staged for the merge notice (never merged).
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        from .garage_merge import flag_sign_out, stage_on_login

        user_logged_in.connect(stage_on_login, dispatch_uid="fitment.stage_garage_merge")
        # FIT.6: at sign-out the cookie made from the account's active profile is cleared.
        user_logged_out.connect(flag_sign_out, dispatch_uid="fitment.garage_sign_out")

    audited_models = (
        "fitmentscheme",
        "fitmentlevel",
        "fitmentnode",
        "fitmentnodealias",
        "fitmentnodemerge",
        "fitmentcategory",
        "productfitmentstate",
        "productfitment",
        "fitmentnodeproposal",
        "garageprofile",
    )
    unaudited_models = {
        "fitmentleveltranslation": "level labels: audited with the level they belong to",
        "fitmentnodetranslation": "a node's name per language: audited as `fitment.node.updated`",
        "fitmentnote": "short operator-defined reasons, no sensitive data; part 2 adds its pages",
        "fitmentnotetranslation": "the text of a note under the translation workflow",
    }
