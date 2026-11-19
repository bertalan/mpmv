"""The public seller name of a vendor (SEO.15): the adapter between `Vendor` and the pure rule
`engine.public_seller_name`, shared by the product graph (SEO.1) and the page graph (SEO.7)."""

from apps.vendors import seller_name
from apps.vendors.models import Vendor

from . import engine

name_facts = seller_name.name_facts
public_name = seller_name.public_name


def person_names(vendor: Vendor) -> frozenset[str]:
    """For the schema check: the names this vendor's seller node must not carry."""
    return engine.natural_person_names(name_facts(vendor))
