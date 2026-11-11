"""Test doubles for the tax app's external boundary, VIES (ADR-0006: our own interfaces only).

VIES is never called from tests: services take a `ViesClient`, and `settings.VIES_CLIENT` can
name one of the no-argument fakes below. The test settings default to `RefusingViesClient`.
"""

import pytest

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
        self.requesters: list[ViesQuery | None] = []

    def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
        self.calls.append(query)
        self.requesters.append(requester)
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

    def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
        raise RuntimeError("unexpected client failure")


class RefusingViesClient:
    """The test settings' `VIES_CLIENT`: a check that forgot its fake fails the test, loudly.

    `pytest.fail` raises an exception that is not an `Exception`, so the services' fail-closed
    `except Exception` cannot turn the refusal into a quiet "VIES unavailable". A test that
    needs an answer passes `client=` or sets `settings.VIES_CLIENT` to one of the fakes above.
    """

    def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
        pytest.fail(
            f"a test asked VIES about {query.full_number} without a fake ViesClient: pass "
            "client= or set settings.VIES_CLIENT (apps.tax.tests.fakes)"
        )
