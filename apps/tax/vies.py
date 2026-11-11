"""The EU VIES VAT number check behind our own `ViesClient` interface (TAX.5, ADR-0003).

`HttpViesClient` asks the VIES REST API (`check-vat-number`) once, with a timeout, and never
raises: VIES down, too slow or answering nonsense is an `unavailable` answer with the reason
in `detail`. It never retries; retries with backoff belong to the background job
`tax.recheck_vat_number`. Caching and the fail-closed decision are in
`apps.tax.services.verify_vat_number`. Tests use the fakes in `apps.tax.tests.fakes`.
"""

import http.client
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol, Self, runtime_checkable

from django.conf import settings
from django.utils.module_loading import import_string

from apps.core.services import NoPlatformOperator, current_operator

from . import engine
from .engine import NotCheckable, ViesAnswer, ViesQuery

logger = logging.getLogger(__name__)

VIES_API_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"

#: Posts a JSON body and returns the JSON object VIES answered, also for an error status.
Transport = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]


@runtime_checkable
class ViesClient(Protocol):
    def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
        """VIES's answer about `query`. An unreachable or slow VIES is an `unavailable`
        answer, never an exception."""
        ...


class ViesHttpError(OSError):
    """VIES answered with an error status and no JSON body."""

    def __init__(self, status: int) -> None:
        super().__init__(f"VIES answered HTTP {status}")
        self.status = status


def _json_object(raw: bytes) -> Mapping[str, Any]:
    answer = json.loads(raw)
    if not isinstance(answer, dict):
        raise ValueError("VIES did not answer with a JSON object")
    return answer


def post_json(url: str, data: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
    """The urllib transport. VIES puts its error codes in the body of 4xx and 5xx answers."""
    request = urllib.request.Request(  # noqa: S310 - the configured https URL of VIES
        url,
        data=json.dumps(data).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return _json_object(response.read())
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                return _json_object(exc.read())
            except ValueError:
                raise ViesHttpError(exc.code) from exc


def _failure_detail(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return engine.DETAIL_TIMEOUT
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
        return engine.DETAIL_TIMEOUT
    if isinstance(exc, ViesHttpError):
        return f"http_{exc.status}"
    if isinstance(exc, ValueError):
        return engine.DETAIL_BAD_RESPONSE
    return engine.DETAIL_CONNECTION  # OSError, or http.client.HTTPException (cut-off answer)


class HttpViesClient:
    """The VIES REST API. One request per check, with a timeout; fails closed.

    The timeout bounds each socket operation (connect, every read), as urllib does, not the
    wall time of the whole check: a server dripping bytes, or a slow DNS lookup, can take longer.

    With a `requester` (the platform operator's own VAT number) VIES returns a consultation
    number (`requestIdentifier`), kept as the answer's `request_id`.
    """

    def __init__(
        self,
        *,
        url: str = VIES_API_URL,
        timeout: float = 8.0,
        requester: ViesQuery | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.requester = requester
        self._transport = transport or post_json

    @classmethod
    def from_settings(cls) -> Self:
        """The client of `settings.VIES_API_URL` and `VIES_TIMEOUT_SECONDS`, asking as the
        current platform operator (its VAT number), or anonymously when there is none."""
        requester = None
        try:
            operator = current_operator()
            requester = engine.vies_query(operator.country, operator.vat_number)
        except (NoPlatformOperator, NotCheckable):
            logger.info("VIES is asked without a requester: no EU platform VAT number")
        return cls(
            url=settings.VIES_API_URL,
            timeout=float(settings.VIES_TIMEOUT_SECONDS),
            requester=requester,
        )

    def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
        """`requester` (the vendor, for an order: D-28) replaces the client's own requester."""
        body = {"countryCode": query.prefix, "vatNumber": query.number}
        asking = requester if requester is not None else self.requester
        if asking is not None:
            body["requesterMemberStateCode"] = asking.prefix
            body["requesterNumber"] = asking.number
        try:
            payload = self._transport(self.url, body, self.timeout)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            detail = _failure_detail(exc)
            logger.warning("VIES check of %s failed: %s (%s)", query.prefix, detail, exc)
            return ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=detail)
        return engine.read_vies_answer(query, payload)


def get_vies_client() -> ViesClient:
    """The configured client (`settings.VIES_CLIENT`), built with `from_settings()` when it
    has one and without arguments otherwise."""
    cls = import_string(settings.VIES_CLIENT)
    factory = getattr(cls, "from_settings", cls)
    client: ViesClient = factory()
    return client


class ViesVatNumberValidator:
    """The vendors' `VatNumberValidator` (VEN.12) on top of the cached, fail-closed check.

    Vendors cannot import tax (spec 01 §1.3): the tax app hands this to
    `apps.vendors.services.check_vat_number`. A number VIES cannot be asked about is `invalid`
    when it is malformed or carries another prefix, `unavailable` otherwise.
    """

    def __init__(self, client: ViesClient | None = None) -> None:
        self.client = client

    def check(self, country_prefix: str, number: str) -> str:
        from . import services  # services builds on this module

        check = services.verify_vat_number(country_prefix, number, client=self.client)
        if check.result is not None:
            return check.result
        if check.reason in (engine.VAT_REASON_MALFORMED, engine.VAT_REASON_PREFIX_MISMATCH):
            return engine.VAT_INVALID
        return engine.VAT_UNAVAILABLE
