"""CORE.24: what the session index keeps of a session (pure helpers) and the critical actions."""

import pytest

from apps.accounts import engine
from apps.core import session_policy

FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0"
CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = CHROME_WINDOWS + " Edg/131.0.0.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)


@pytest.mark.parametrize(
    ("agent", "browser", "system"),
    [
        (FIREFOX_MAC, "Firefox", "macOS"),
        (CHROME_WINDOWS, "Chrome", "Windows"),
        (EDGE_WINDOWS, "Edge", "Windows"),
        (SAFARI_IPHONE, "Safari", "iOS"),
        (CHROME_ANDROID, "Chrome", "Android"),
        ("curl/8.7.1", "", ""),
        ("", "", ""),
        (None, "", ""),
    ],
)
def test_the_device_summary_names_a_browser_family_and_a_system_only(
    agent: str | None, browser: str, system: str
) -> None:
    summary = engine.device_summary(agent)
    assert (summary.browser, summary.system) == (browser, system)


def test_the_device_summary_keeps_nothing_of_the_header_itself() -> None:
    summary = engine.device_summary(FIREFOX_MAC + " my-secret-build-42")
    assert "132" not in summary.browser + summary.system
    assert "secret" not in summary.browser + summary.system


@pytest.mark.parametrize(
    ("ip", "network"),
    [
        ("203.0.113.77", "203.0.113.0/24"),
        ("2001:db8:1234:5678::1", "2001:db8:1234::/48"),
        ("::ffff:198.51.100.9", "198.51.100.0/24"),
        ("not an address", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_the_network_prefix_never_keeps_the_full_address(ip: str | None, network: str) -> None:
    assert engine.network_prefix(ip) == network


@pytest.mark.parametrize("action", ["sessions_end_user", "sessions_end_all"])
def test_ending_sessions_is_critical_and_on_the_floor(action: str) -> None:
    assert session_policy.DEFAULT_POLICY.group_of(action) == session_policy.GROUP_CRITICAL
    assert action in session_policy.FLOOR_CRITICAL_ACTIONS
    with pytest.raises(ValueError, match="cannot be relaxed"):
        session_policy.parse_policy({"actions": {"sensitive": [action]}})
