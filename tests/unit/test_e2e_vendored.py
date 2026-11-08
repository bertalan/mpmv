"""THM.5: axe-core is vendored for the e2e suite, pinned and hashed (spec 14 §14.11).

The accessibility gate injects a pinned copy of axe-core into the page under test; nothing is
fetched from a CDN. `tests/e2e/vendor/VENDORED.toml` records the version, source URL, SHA-256
and licence, checked like the runtime vendored files (THM.3). axe-core is MPL-2.0: allowed for
test tooling, which is never served by the site.
"""

from apps.themes.vendored import check_vendored, read_vendored
from tests.e2e.axe import AXE_TAGS, E2E_VENDOR_DIR, TEST_TOOL_LICENCES


def test_the_vendored_test_tools_match_their_recorded_hashes() -> None:
    assert check_vendored(E2E_VENDOR_DIR, allowed=TEST_TOOL_LICENCES) == []


def test_axe_core_is_pinned() -> None:
    (axe,) = [entry for entry in read_vendored(E2E_VENDOR_DIR) if entry.name == "axe-core"]
    assert axe.path == "axe.min.js"
    assert axe.license == "MPL-2.0"
    assert axe.version in axe.source


def test_mpl_is_not_allowed_for_files_the_site_serves() -> None:
    assert check_vendored(E2E_VENDOR_DIR) != []


def test_the_axe_run_uses_the_spec_tags() -> None:
    assert AXE_TAGS == ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")
