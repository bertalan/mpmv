"""Publication checks owned by other apps (spec 03 §3.2 "Rules", CAT.10).

Some publishing rules belong to apps that sit above the catalog in the dependency graph (spec
01 §1.3): GPSR data is `apps.compliance` (CMP.3). The catalog cannot import them, so those apps
register a check here from their `AppConfig.ready()`, and the publishing services of CAT.10 call
`check_publishable(product)` before a product goes live. A check returns the messages that block
publication, or nothing.
"""

from collections.abc import Callable, Sequence

from django.core.exceptions import ValidationError

from .models import Product

PublicationCheck = Callable[[Product], Sequence[str]]

_CHECKS: dict[str, PublicationCheck] = {}


def register_publication_check(name: str, check: PublicationCheck) -> None:
    """Add `check` under `name`; registering the same name again replaces it (reloads)."""
    _CHECKS[name] = check


def publication_blockers(product: Product) -> dict[str, tuple[str, ...]]:
    """The messages that block publishing `product`, by check; empty when it can go live."""
    blockers: dict[str, tuple[str, ...]] = {}
    for name, check in _CHECKS.items():
        messages = tuple(str(message) for message in check(product))
        if messages:
            blockers[name] = messages
    return blockers


def check_publishable(product: Product) -> None:
    """Raise ValidationError, keyed by check name, unless every registered check passes."""
    blockers = publication_blockers(product)
    if blockers:
        raise ValidationError(
            {
                name: [ValidationError(message, code=f"{name}_missing") for message in messages]
                for name, messages in blockers.items()
            }
        )
