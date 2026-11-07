"""Test doubles for the tax app's external boundary, VIES (ADR-0006: our own interfaces only).

VIES is never called from tests: services take a `ViesClient`, and `settings.VIES_CLIENT` can
name one of the no-argument fakes below.
"""

from apps.tax import engine
from apps.tax.engine import ViesAnswer, ViesQuery


class FakeViesClient:
    """A `ViesClient` answering from a table keyed by the full number (`DE123456789`)."""

    def __init__(
        self,
        answers: dict[str, ViesAnswer] | None = None,
        default: ViesAnswer | None = None,
    ) -> None:
        self.answers = answers or {}
        self.default = default or ViesAnswer(result=engine.VAT_VALID, request_id="WAPITEST0001")
        self.calls: list[ViesQuery] = []

    def check(self, query: ViesQuery) -> ViesAnswer:
        self.calls.append(query)
        return self.answers.get(query.full_number, self.default)


DOWN = ViesAnswer(result=engine.VAT_UNAVAILABLE, detail="MS_UNAVAILABLE")
TIMED_OUT = ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=engine.DETAIL_TIMEOUT)
INVALID = ViesAnswer(result=engine.VAT_INVALID, request_id="WAPITEST0002")


class ValidViesClient(FakeViesClient):
    """VIES confirms every number."""


class DownViesClient(FakeViesClient):
    """VIES (or the member state's service) does not answer."""

    def __init__(self) -> None:
        super().__init__(default=DOWN)


class BrokenViesClient:
    """A client that breaks its contract and raises: the service must still fail closed."""

    def check(self, query: ViesQuery) -> ViesAnswer:
        raise RuntimeError("unexpected client failure")
