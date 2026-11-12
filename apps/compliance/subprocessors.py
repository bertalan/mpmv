"""The sub-processor register (CMP.4, spec 11 "Record of processing activities").

A reviewed data file, not a table: adding a provider is a code change that goes through review
and appears on the public page at `privacy/sub-processors/`. Entries name the kind of service and
the data it receives; the operator's DPA and the transfer safeguard are what the privacy policy
(CMP.31) refers to. Product names are limited to what the spec itself names.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_noop as _


@dataclass(frozen=True)
class SubProcessor:
    key: str
    category: str
    name: str
    purpose: str
    data: str
    location: str
    safeguard: str
    public: bool = True


REGISTER: tuple[SubProcessor, ...] = (
    SubProcessor(
        "payments",
        "payments",
        "Stripe",
        _("Card and wallet payments, payouts to vendors"),
        _("Payment references, amount, country, billing name and email"),
        _("EU and USA"),
        _("DPA and standard contractual clauses"),
    ),
    SubProcessor(
        "carriers",
        "carriers",
        _("Shipping carriers"),
        _("Delivery of the parcels the vendors send"),
        _("Recipient name, delivery address, phone number and email"),
        _("EU"),
        _("DPA of each carrier"),
    ),
    SubProcessor(
        "email",
        "email",
        _("Transactional email provider"),
        _("Order and account emails"),
        _("Email address, name and the content of the message"),
        _("EU"),
        _("DPA"),
    ),
    SubProcessor(
        "ai",
        "ai",
        _("AI provider"),
        _("Drafts of translations and product texts reviewed by staff"),
        _("Catalogue and page texts; no customer account data"),
        _("EU and USA"),
        _("DPA and standard contractual clauses"),
    ),
    SubProcessor(
        "hosting",
        "hosting",
        _("Hosting provider"),
        _("Servers, database, file storage and backups"),
        _("All data the platform holds, encrypted at rest"),
        _("EU"),
        _("DPA"),
    ),
    SubProcessor(
        "geocoding",
        "geocoding",
        _("Geocoding provider"),
        _("Coordinates of event places entered by editors"),
        _("Place address text; no customer data"),
        _("EU"),
        _("Public data only"),
    ),
    SubProcessor(
        "map_tiles",
        "map_tiles",
        _("Map tile provider"),
        _("Map images shown on event pages"),
        _("The visitor's IP address and the tiles requested"),
        _("EU"),
        _("Tiles are proxied through the platform; no cookies"),
    ),
)


def public_register() -> tuple[SubProcessor, ...]:
    return tuple(entry for entry in REGISTER if entry.public)
