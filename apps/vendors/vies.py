"""Our interface to the EU VIES VAT number check (VEN.12, ADR-0003).

Vendor code depends on this protocol only. A real client and the periodic re-check are a
follow-up; tests use a fake (`apps.vendors.tests.fakes`). VIES is never called from tests.
"""

from typing import Protocol


class VatNumberValidator(Protocol):
    def check(self, country_prefix: str, number: str) -> str:
        """The VIES answer for `number` (without its prefix) under `country_prefix` (EL for
        Greece): `engine.VIES_VALID`, `VIES_INVALID`, or `VIES_UNAVAILABLE` when VIES or the
        member state's service does not answer. An unavailable service is not an error."""
        ...
