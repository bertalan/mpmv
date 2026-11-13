"""payments.E004: LIVE_DATA is refused while the vendor transfers (PAY.3) are not wired."""

from typing import Any

from django.core.checks import Error

from apps.payments.checks import check_transfers_wired


def ids(messages: list[Any]) -> list[str]:
    return [str(m.id) for m in messages if isinstance(m, Error)]


def test_live_data_without_wired_transfers_is_an_error(settings: Any) -> None:
    settings.LIVE_DATA, settings.PAYMENTS_TRANSFERS_WIRED = True, False
    found = check_transfers_wired()
    assert ids(found) == ["payments.E004"]
    assert "against D-08" in found[0].msg


def test_stage_is_not_affected(settings: Any) -> None:
    settings.LIVE_DATA, settings.STAGE, settings.PAYMENTS_TRANSFERS_WIRED = False, True, False
    assert check_transfers_wired() == []


def test_live_data_with_wired_transfers_is_fine(settings: Any) -> None:
    settings.LIVE_DATA, settings.PAYMENTS_TRANSFERS_WIRED = True, True
    assert check_transfers_wired() == []
