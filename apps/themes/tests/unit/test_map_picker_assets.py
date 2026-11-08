"""EVT.13: the static side of the map picker (spec 15 "Map picker", "Maps").

- Leaflet 1.9.4 is vendored under `static/vendor/leaflet/` with its hash and its BSD-2-Clause
  licence in `VENDORED.toml` (THM.3): never a CDN.
- The picker template passes the CSP lint (no inline script, style or handler).
- `events/map.js` is a module that names no third-party host: tiles, geocoding and Leaflet all
  come from the site itself (regression case 23).
"""

from pathlib import Path

from apps.themes.csp_lint import lint_template
from apps.themes.vendored import VENDOR_DIR, check_vendored, read_vendored

# The events app owns the picker; its files are checked here, where the theme gates live
# (apps.events may not import apps.themes).
EVENTS_DIR = Path(__file__).resolve().parents[4] / "apps" / "events"
MAP_JS = EVENTS_DIR / "static" / "events" / "map.js"
MAP_CSS = EVENTS_DIR / "static" / "events" / "map.css"
PICKER_TEMPLATE = EVENTS_DIR / "templates" / "events" / "admin" / "map_picker_panel.html"

LEAFLET_FILES = {
    "leaflet/leaflet.js",
    "leaflet/leaflet.js.map",
    "leaflet/leaflet.css",
    "leaflet/LICENSE",
    "leaflet/images/marker-icon.png",
    "leaflet/images/marker-icon-2x.png",
    "leaflet/images/marker-shadow.png",
    "leaflet/images/layers.png",
    "leaflet/images/layers-2x.png",
}


def test_leaflet_1_9_4_is_vendored_with_its_licence_and_hashes() -> None:
    entries = {entry.path: entry for entry in read_vendored(VENDOR_DIR)}
    assert set(entries) >= LEAFLET_FILES
    for path in LEAFLET_FILES:
        entry = entries[path]
        assert entry.name == "leaflet", path
        assert entry.version == "1.9.4", path
        assert entry.license == "BSD-2-Clause", path
        assert entry.source.startswith("https://unpkg.com/leaflet@1.9.4/"), path
    assert check_vendored(VENDOR_DIR) == []


def test_the_vendored_leaflet_licence_is_the_bsd_2_clause_text() -> None:
    text = (VENDOR_DIR / "leaflet" / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("BSD 2-Clause License")


def test_the_picker_template_passes_the_csp_lint() -> None:
    assert lint_template(PICKER_TEMPLATE.read_text(encoding="utf-8")) == []


def test_the_map_module_names_no_third_party_host() -> None:
    source = MAP_JS.read_text(encoding="utf-8")
    assert "http://" not in source
    assert "https://" not in source
    assert "eval(" not in source
    assert "innerHTML" not in source  # candidate labels are provider text: set as text only
    assert "export function initPicker" in source


def test_the_map_styles_need_no_inline_style() -> None:
    assert ".mpmv-map-picker__map" in MAP_CSS.read_text(encoding="utf-8")
