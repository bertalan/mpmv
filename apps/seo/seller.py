"""The public seller name of a vendor (SEO.15): the adapter between `Vendor` and the pure rule
`engine.public_seller_name`, shared by the product graph (SEO.1) and the page graph (SEO.7)."""

from apps.vendors.models import Vendor

from . import engine


def name_facts(vendor: Vendor) -> engine.VendorNameFacts:
    sole_trader = vendor.legal_form == Vendor.LegalForm.SOLE_PROPRIETORSHIP
    unspecified_individual = not vendor.legal_form and bool(vendor.first_name and vendor.last_name)
    return engine.VendorNameFacts(
        display_name=str(vendor.display_name),
        legal_name=str(vendor.legal_name),
        first_name=str(vendor.first_name),
        last_name=str(vendor.last_name),
        natural_person=sole_trader or unspecified_individual,
    )


def public_name(vendor: Vendor) -> str | None:
    return engine.public_seller_name(name_facts(vendor))


def person_names(vendor: Vendor) -> frozenset[str]:
    """For the schema check: the names this vendor's seller node must not carry."""
    return engine.natural_person_names(name_facts(vendor))
