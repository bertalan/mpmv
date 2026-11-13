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

    audited_models = (
        "fitmentscheme",
        "fitmentlevel",
        "fitmentnode",
        "fitmentnodealias",
        "fitmentnodemerge",
        "fitmentcategory",
    )
    unaudited_models = {
        "fitmentleveltranslation": "level labels: audited with the level they belong to",
        "fitmentnodetranslation": "a node's name per language: audited as `fitment.node.updated`",
        "fitmentnote": "short operator-defined reasons, no sensitive data; part 2 adds its pages",
        "fitmentnotetranslation": "the text of a note under the translation workflow",
    }
