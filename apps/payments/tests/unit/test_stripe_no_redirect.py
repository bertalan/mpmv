"""SEC.5 follow-up: the Stripe client never follows a redirect.

`HttpStripeClient` speaks HTTP through its default transport; a local server answers every call
with a redirect to another host, and the client must stop at the first answer.
"""

import secrets
import urllib.request

import pytest

from apps.core.outbound import RedirectRefused
from apps.core.tests.redirect_server import redirect_server
from apps.payments import stripe_client
from apps.payments.stripe_client import HttpStripeClient, StripeError


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_the_client_refuses_every_redirect(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    secret = secrets.token_urlsafe(24)
    with redirect_server(status) as server:
        monkeypatch.setattr(stripe_client, "API_BASE", f"{server.base_url}/v1")
        client = HttpStripeClient(secret_key=secret)
        with pytest.raises(StripeError) as refused:
            client.get_account("acct_test")
    assert server.paths == ["/v1/accounts/acct_test"]
    assert secret not in str(refused.value)


def test_the_default_transport_raises_the_typed_refusal() -> None:
    with redirect_server() as server:
        request = urllib.request.Request(f"{server.base_url}/v1/balance", method="GET")  # noqa: S310 (local test server)
        with pytest.raises(RedirectRefused):
            HttpStripeClient(secret_key=secrets.token_urlsafe(24)).transport(request)
    assert server.paths == ["/v1/balance"]
