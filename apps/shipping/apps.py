from django.apps import AppConfig


class ShippingConfig(AppConfig):
    name = "apps.shipping"
    label = "shipping"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). The admin audits every row it writes.
    audited_models = (
        "carrier",
        "countrygroup",
        "shippingzone",
        "shippingmethod",
        "weightband",
        "pickupoption",
    )
    unaudited_models = {
        "carriertranslation": "display labels under the translation workflow, no price data",
        "countrygrouptranslation": "display names under the translation workflow; the member "
        "countries are on the audited group",
        "shippingmethodtranslation": "display names under the translation workflow, no price data",
        "pickupoptiontranslation": "display labels under the translation workflow, no price data",
        "shippingconfigversion": "append-only snapshots of the configuration: the rows are the "
        "history (view-only admin)",
    }
