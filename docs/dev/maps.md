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
| `MAP_TILE_URL` | `MPMV_MAP_TILE_URL` | `/maps/tiles/{z}/{x}/{y}.png`, the tile proxy (EVT.14) |
| `MAP_TILE_ATTRIBUTION` | `MPMV_MAP_TILE_ATTRIBUTION` | `© OpenStreetMap contributors` |
| `MAP_TILE_ATTRIBUTION_URL` | `MPMV_MAP_TILE_ATTRIBUTION_URL` | `https://www.openstreetmap.org/copyright` |
| `MAP_TILE_UPSTREAM_URL` | `MPMV_MAP_TILE_UPSTREAM_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png`: https template the proxy fetches from |
| `MAP_TILE_USER_AGENT` | `MPMV_MAP_TILE_USER_AGENT` | empty: product, site URL and geocoding contact |
| `MAP_TILE_UPSTREAM_DAILY_LIMIT` | `MPMV_MAP_TILE_UPSTREAM_DAILY_LIMIT` | `100000` upstream requests per UTC day, then 503 |
| `MAP_TILE_MIN_ZOOM`, `MAP_TILE_MAX_ZOOM` | none | `2`, `18` |
| `MAP_TILE_SINGLE_FLIGHT_WAIT` | none | `8` seconds a request waits for a concurrent fetch of the same tile, then 503 |
| `MAP_TILE_IP_LIMIT`, `MAP_TILE_IP_WINDOW` | none | `600` tiles per `60` s per client IP |
| `MAP_TILE_BROWSER_MAX_AGE` | none | 7 days: `Cache-Control` for browsers and the CDN |
| `MAP_TILE_CACHE_STORAGE` | none | `default`: the `STORAGES` alias (object storage on LIVE) |
| `MAP_DEFAULT_VIEW` | none | `(48.0, 10.0, 4)`: latitude, longitude, zoom of a map without a point |

`MAP_TILE_URL` is a Leaflet template with `{z}`, `{x}` and `{y}`. The intended value is the
same-origin tile proxy of EVT.14, `/maps/tiles/{z}/{x}/{y}.png`, which needs no CSP change.
Until that proxy exists, leave it empty: the picker still works (click, drag, inputs, "Find
coordinates") on a blank map. An absolute URL must be `https://` on one fixed host (no `{s}`
subdomains); `mpmv.settings.env.map_tile_origin` then adds that one origin to the CSP
`img-src`. Anything else stops the settings from loading. Never point it at
`tile.openstreetmap.org` directly in production: the visitor's IP address would reach a third
party, which is why EVT.14 exists.

## Tile proxy (EVT.14)

`apps/core/maps`: `/maps/tiles/{z}/{x}/{y}.png` on our own domain. `engine.parse_tile` accepts
canonical ASCII integers only, `MAP_TILE_MIN_ZOOM <= z <= MAP_TILE_MAX_ZOOM` and
`0 <= x, y < 2**z`; anything else is a 404 before any database or upstream call. Then the per-IP
limit (429 with `Retry-After`, client IP from `client_ip`), then the cache in object storage
(`map-tiles/<hash of the upstream template>/z/x/y.tile`, so switching upstream never serves the
old provider's tiles), kept 7 days or the upstream's `max-age`/`Expires` if longer (at most a
year). A miss counts one upstream request (a per-day `Throttle` row, read with
`services.upstream_requests()`; past `MAP_TILE_UPSTREAM_DAILY_LIMIT` the proxy answers 503 and
sends nothing), fetches through the `TileUpstream` protocol (`HttpTileUpstream`, a fake in tests)
and caches the PNG. A cold tile is single-flight: the miss path takes a PostgreSQL session advisory lock keyed on
`hashtextextended(cache path)` (`services._single_flight`, polling `pg_try_advisory_lock`), so a
burst for one tile makes one upstream call and one count; the others wait, re-read the cache and
answer from it. After `MAP_TILE_SINGLE_FLIGHT_WAIT` seconds a waiter answers 503 with
`Retry-After: 2` and never calls the upstream. The lock covers only fetch and store (never the
response stream) and only that tile, so other tiles do not wait. Upstream errors are 502, timeouts 504, a non-PNG answer 502, none
cached.
The daily cap counts attempts, failed fetches included. Requests without a client IP share one
rate-limit bucket (fine behind the configured proxy). `TileResponseNeutralMiddleware` strips
cookies and `Vary: Accept-Language` from tiles, and `HttpTileUpstream` never follows a redirect
(a 3xx is a 502, not cached).
Responses carry `Cache-Control: public, max-age=604800`; refusals `no-store`. The upstream URL may
hold a provider key: it is never logged and never part of a cache path. OSM's public tile server
allows light use only: watch the counter and switch the upstream before traffic grows.

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

## Public maps (EVT.15)

`events/map.js` also builds the public maps: `initEventMap` for `[data-event-map]` on the event
page and `initIndexMap` for `[data-events-map]` on `/events/`. Templates: `_event_map.html`,
`_index_map.html`, `_map_assets.html` (Leaflet and the module, only on a page with a map). The
context comes from `listing.map_context` and the pure `engine.directions_links` / `search_url`.

- Event page: a map only with both coordinates. Order in the markup: the facts (with the text
  address), a "Skip map" link, the labelled region (`role="region"`, `aria-label`), then the
  directions links (Google Maps, OpenStreetMap, `geo:`), which are also the skip link's target.
  The region holds the server-rendered fallback (place, address, attribution) until the module
  replaces it. With an address and no coordinates the "Where" row gets one "Open in maps" search
  link (`data-map-search`) and no map; with neither, nothing.
- The marker is keyboard focusable (title = venue name, Enter opens the popup). Scroll-wheel
  zoom is off until the map has focus (`data-wheel-zoom` shows the state). Under
  `prefers-reduced-motion: reduce` the zoom, fade and marker animations are off.
- Index: the map is an enhancement, hidden without JavaScript and not rendered when no listed
  event has coordinates. It reads `data-lat`/`data-lng` from the listed items (the current page
  of results, after the filter); events without coordinates stay in the list only.
- Tiles come from `MAP_TILE_URL` (the EVT.14 proxy). Leaflet cancels dropped tiles with a
  `data:` GIF, which `img-src 'self'` blocks; `map.js` replaces two methods of its tile layer
  so no `data:` URL is used and the vendored file stays unchanged.
- Strings: `_event_map.html` and `_index_map.html` are in the I18N.7 catalog scope.

## Follow-ups

- EVT.4: the vendor-area event form uses the same picker: render
  `events/admin/map_picker_panel.html` (or a vendor-area copy with the same data attributes)
  next to the form's inputs and add `map_assets()` to the page media. The "Find coordinates"
  endpoint already admits vendor members. Render the form's `picked_latitude` and
  `picked_longitude` hidden inputs too. EVT.4's done-when checks the vendor half: a vendor sets
  a point by click, by drag and by typing, and the saved values match.
- EVT.14 is done: see "Tile proxy" below; `MAP_TILE_URL` now defaults to the proxy.
