# Maps and the map picker

Backlog EVT.13 (spec 15 "Map picker" and "Maps"). The public event maps (EVT.15) and the tile
proxy (EVT.14) build on what is described here.

## What is where

| Piece | Path |
|---|---|
| Leaflet 1.9.4, vendored (THM.3) | `apps/themes/static/vendor/leaflet/`, entries in `VENDORED.toml` (SHA-256, BSD-2-Clause, licence text in `leaflet/LICENSE`) |
| Shared map module | `apps/events/static/events/map.js` (ES module: `createMap`, `initPicker`, `validPoint`) |
| Map styles | `apps/events/static/events/map.css` (after `leaflet.css`) |
| Admin picker panel | `apps.events.panels.MapPickerPanel`, template `events/admin/map_picker_panel.html` |
| Assets and settings for any page with a map | `apps.events.panels.map_assets()`, `map_settings()` |
| Checking a picked point | `apps.events.services.resolve_picked_point`, `apps.core.geocoding.services.cached_candidate` |

The picker is a container with data attributes only: the ids of the form inputs it reads and
writes (`data-latitude-input`, `data-longitude-input`, `data-source-input`, and the address
inputs), the "Find coordinates" URL, the tile settings and the translated messages. `map.js`
finds every `[data-map-picker]` on the page and builds the map. No inline script, no inline
style, no third-party host.

## How the picker behaves

- A click on the map places the point; the marker can be dragged. Both write the latitude and
  longitude inputs (6 decimal places, what the page stores) and set `geo_source` to `map`.
- Typing in the inputs moves the marker and centres the map (`geo_source = "manual"`). The
  inputs are the keyboard alternative to clicking and dragging (WCAG 2.2 SC 2.5.7) and work
  without JavaScript: a point typed with no source counts as `manual`.
- "Find coordinates" posts the address fields to `/geocoding/find/` (EVT.12) with the CSRF
  token. The candidates are listed as buttons (the provider's label set as text, never as
  markup); choosing one sets the point and `geo_source = "geocoded"`.
- The form never takes geocoding metadata from the request. On save, a `geocoded` point must
  be a candidate of an unexpired cached lookup of the form's address at that point (six decimal
  places); the provider, precision and confidence then come from the cache. Otherwise the form
  shows an error on "geo source". An unchanged geocoded point is kept even after its cache
  entry expired. Clearing both inputs clears the source and the metadata.
- Choosing a candidate also writes its point to the hidden `picked_latitude` and
  `picked_longitude` inputs, which later drags, clicks and typing leave alone. If the author
  moves the point before saving (spec 15 "Confirmation"), the form checks that chosen point
  against the cache the same way and stores the moved point with its own source (`map` or
  `manual`) and the candidate's provider, precision, confidence and time. A chosen point that no
  cached lookup returned adds no metadata. This is the same rule as moving the geocoded point of
  a saved page, so pick, drag, save and pick, save, drag, save give the same metadata.

## Settings

| Setting | Environment variable | Default |
|---|---|---|
| `MAP_TILE_URL` | `MPMV_MAP_TILE_URL` | empty: no tile layer |
| `MAP_TILE_ATTRIBUTION` | `MPMV_MAP_TILE_ATTRIBUTION` | `© OpenStreetMap contributors` |
| `MAP_TILE_ATTRIBUTION_URL` | `MPMV_MAP_TILE_ATTRIBUTION_URL` | `https://www.openstreetmap.org/copyright` |
| `MAP_DEFAULT_VIEW` | none | `(48.0, 10.0, 4)`: latitude, longitude, zoom of a map without a point |

`MAP_TILE_URL` is a Leaflet template with `{z}`, `{x}` and `{y}`. The intended value is the
same-origin tile proxy of EVT.14, `/maps/tiles/{z}/{x}/{y}.png`, which needs no CSP change.
Until that proxy exists, leave it empty: the picker still works (click, drag, inputs, "Find
coordinates") on a blank map. An absolute URL must be `https://` on one fixed host (no `{s}`
subdomains); `mpmv.settings.env.map_tile_origin` then adds that one origin to the CSP
`img-src`. Anything else stops the settings from loading. Never point it at
`tile.openstreetmap.org` directly in production: the visitor's IP address would reach a third
party, which is why EVT.14 exists.

## Tests

- Unit: the vendored files and their hashes, the CSP lint of the picker template, the map
  module names no third-party host, the tile URL rule.
- Integration: the panel renders the picker and loads only vendored assets; the form rules
  above.
- E2E (`tests/e2e/test_event_map_picker.py`): click, drag, typing and "Find coordinates" in the
  admin, with the saved values compared with the inputs, and regression case 23 (no CSP
  violation, no request outside the site). The tiles are answered by a Playwright route mock on
  the same-origin path; no test reaches the network.

## Decision: our own picker, not wagtailgeowidget

Spec 15 asks EVT.13 to use `wagtailgeowidget` only if it runs with the vendored Leaflet, without
inline script or a CDN, with our tile proxy and with our server-side geocoder. Version 9.1.0
(the latest at the time of writing) was checked against those rules:

| Rule | wagtailgeowidget 9.1.0 |
|---|---|
| No inline script | Met: it initialises through Stimulus data attributes. |
| Vendored Leaflet, no CDN | Not met: `LeafletWidget.media` hard-codes `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js` and `.css`. Overriding needs a subclass of the widget. |
| Our tile proxy | Met through `GEO_WIDGET_LEAFLET_TILE_LAYER`, but its default is the public OSM tile servers. |
| Server-side geocoding | Not met: the geocoder field calls Nominatim (or Google, Mapbox) from the browser with `fetch`. That breaks the server-side geocoding rule, the per-user limit and the cache of EVT.12, and regression case 23. |
| Our data model | Not met: it stores one `SRID=4326;POINT(lng lat)` string, while the page has two decimal fields and `geo_source` with the geocoding metadata. |

Using it would mean replacing its media, its geocoder and its storage, so little of it would
remain. The own module is about 300 lines, runs the same code in the admin, the vendor area
(EVT.4) and the public maps (EVT.15), and keeps the dependency list shorter.

## Follow-ups

- EVT.4: the vendor-area event form uses the same picker: render
  `events/admin/map_picker_panel.html` (or a vendor-area copy with the same data attributes)
  next to the form's inputs and add `map_assets()` to the page media. The "Find coordinates"
  endpoint already admits vendor members. Render the form's `picked_latitude` and
  `picked_longitude` hidden inputs too. EVT.4's done-when checks the vendor half: a vendor sets
  a point by click, by drag and by typing, and the saved values match.
- EVT.14: set `MPMV_MAP_TILE_URL=/maps/tiles/{z}/{x}/{y}.png` once the proxy is live.
- EVT.15: the public maps call `createMap` from the same module.
