"""SEC.5 follow-up: the VIES transport never follows a redirect (local server, no network)."""

import pytest

from apps.core.tests.redirect_server import redirect_server
from apps.tax.vies import ViesHttpError, post_json


def test_post_json_refuses_a_redirect_to_another_host() -> None:
    with redirect_server(302) as server, pytest.raises(ViesHttpError) as refused:
        post_json(f"{server.base_url}/check-vat-number", {"countryCode": "DE"}, 5)
    assert refused.value.status == 302
    assert server.paths == ["/check-vat-number"]
