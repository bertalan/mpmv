"""EVT.14: the map attribution and tile URL come from settings, not code."""

from typing import Any

from apps.events.panels import map_settings


def test_the_attribution_comes_from_the_setting(settings: Any) -> None:
    settings.MAP_TILE_ATTRIBUTION = "\u00a9 Example tiles"
    settings.MAP_TILE_URL = "/maps/tiles/{z}/{x}/{y}.png"
    data = map_settings()
    assert data["attribution"] == "\u00a9 Example tiles"
    assert data["tile_url"] == "/maps/tiles/{z}/{x}/{y}.png"
