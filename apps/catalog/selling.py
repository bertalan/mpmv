"""Per-destination sale checks owned by other apps (spec 03 §3.2 "Rules", spec 07 ORD.3).

A published product may still not be sold into some countries: GPSR warnings must exist in a
language of the destination (`apps.compliance`, CMP.3), and later the EPR checks of CMP.11.
Those apps sit above the catalog, cart and orders in the dependency graph (spec 01 §1.3), so they
register a check here from their `AppConfig.ready()`, and the checkout (ORD.3) calls
`sale_blockers(product, country)` for each line. A check returns the messages that block the
sale into `country`, or nothing. Unlike the publication checks, a blocked destination leaves the
product published and on sale elsewhere.
"""

from collections.abc import Callable, Sequence

from .models import Product

SaleCheck = Callable[[Product, str], Sequence[str]]

_CHECKS: dict[str, SaleCheck] = {}


def register_sale_check(name: str, check: SaleCheck) -> None:
    """Add `check` under `name`; registering the same name again replaces it (reloads)."""
    _CHECKS[name] = check


def sale_blockers(product: Product, country: str) -> dict[str, tuple[str, ...]]:
    """The messages that block selling `product` into `country`, by check; empty when allowed."""
    blockers: dict[str, tuple[str, ...]] = {}
    for name, check in _CHECKS.items():
        messages = tuple(str(message) for message in check(product, country))
        if messages:
            blockers[name] = messages
    return blockers
