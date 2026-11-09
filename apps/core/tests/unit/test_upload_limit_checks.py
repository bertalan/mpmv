"""SEC.2: the upload caps are settings, checked at startup (core.E601, core.E602)."""

from collections.abc import Sequence

import pytest
from django.test import override_settings

from apps.core.checks import check_image_upload_limits

DEFAULTS = {
    "WAGTAILIMAGES_MAX_UPLOAD_SIZE": 25 * 1024 * 1024,
    "WAGTAILIMAGES_MAX_IMAGE_PIXELS": 60_000_000,
    "MPMV_MAX_ANIMATION_PIXELS": 16_000_000,
    "MPMV_MAX_STORED_IMAGE_PIXELS": 40_000_000,
    "MPMV_MAX_IMAGE_SIDE": 16_000,
    "MPMV_MAX_IMAGE_FRAMES": 300,
    "MPMV_IMAGE_DECODE_WAIT_SECONDS": 30.0,
}


def check_ids(**overrides: object) -> Sequence[str]:
    with override_settings(**{**DEFAULTS, **overrides}):
        return [message.id or "" for message in check_image_upload_limits(None)]


def test_the_decided_defaults_pass() -> None:
    assert check_ids() == []


@pytest.mark.parametrize(
    "name",
    [name for name in DEFAULTS],
)
@pytest.mark.parametrize("value", [0, -1, None, "60"])
def test_every_cap_must_be_a_positive_number(name: str, value: object) -> None:
    assert check_ids(**{name: value}) == ["core.E601"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"MPMV_MAX_STORED_IMAGE_PIXELS": 60_000_001},  # stored larger than decoded
        {"MPMV_MAX_ANIMATION_PIXELS": 60_000_001},  # an animation above the still cap
    ],
)
def test_the_stored_and_animation_caps_fit_within_the_decode_cap(
    overrides: dict[str, object],
) -> None:
    assert check_ids(**overrides) == ["core.E602"]
