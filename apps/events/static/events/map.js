// Maps of the events app (EVT.13, spec 15 "Maps" and "Map picker").
//
// One module for every map of an event: the picker of the Wagtail admin "Where" panel now, the
// vendor-area event form (EVT.4) and the public event and index maps (EVT.15) on the same code
// later. It needs the vendored Leaflet (`vendor/leaflet/leaflet.js`, the global `L`) loaded
// before it and reads everything from data attributes: no inline script and no third-party host
// (CSP `script-src 'self'`, regression case 23). Tiles come from `data-tile-url` (the
// MAP_TILE_URL setting, the same-origin tile proxy of EVT.14); without one the map has no tile
// layer and still takes clicks and drags. Geocoding runs on the server only: "Find coordinates"
// posts the address to our endpoint (EVT.12), never to a provider.

const PLACES = 6; // decimal places stored on the page (about 0.1 m)
const POINT_ZOOM = 16;

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function number(value) {
  const text = String(value ?? "").trim();
  if (text === "") {
    return null;
  }
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

// A point within the latitude and longitude ranges the page accepts, or null.
export function validPoint(latitude, longitude) {
  if (latitude === null || longitude === null) {
    return null;
  }
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
    return null;
  }
  return [latitude, longitude];
}

// Leaflet takes the attribution as markup: the text and the URL come from the settings, so they
// are escaped here before they become a link.
function escape(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function attributionLink(text, url) {
  if (!text) {
    return "";
  }
  if (!url) {
    return escape(text);
  }
  return `<a href="${escape(url)}">${escape(text)}</a>`;
}

// Leaflet cancels a tile it no longer needs by pointing the image at a `data:` GIF, which the
// page's CSP (`img-src 'self'`) blocks and reports. These two methods do the same cleanup
// without a `data:` URL: the tile image is detached, so the browser drops its request. Nothing
// else of Leaflet changes and the vendored file stays byte-identical.
function tileLayer(url, options) {
  const L = window.L;
  const Layer = L.TileLayer.extend({
    _removeTile(key) {
      return L.GridLayer.prototype._removeTile.call(this, key);
    },
    _abortLoading() {
      for (const key of Object.keys(this._tiles)) {
        const { el, coords } = this._tiles[key];
        if (coords.z !== this._tileZoom && !el.complete) {
          el.onload = L.Util.falseFn;
          el.onerror = L.Util.falseFn;
          L.DomUtil.remove(el);
          delete this._tiles[key];
          this.fire("tileabort", { tile: el, coords });
        }
      }
    },
  });
  return new Layer(url, options);
}

// A Leaflet map in `element`, centred on [lat, lng] at `zoom`, with the configured tiles.
export function createMap(
  element,
  { lat, lng, zoom, tileUrl, attribution, attributionUrl, wheelZoom = true },
) {
  const L = window.L;
  const still = reducedMotion();
  const map = L.map(element, {
    center: [lat, lng],
    zoom,
    minZoom: 2,
    maxZoom: 18,
    scrollWheelZoom: wheelZoom,
    zoomAnimation: !still,
    fadeAnimation: !still,
    markerZoomAnimation: !still,
    inertia: !still,
  });
  if (tileUrl) {
    tileLayer(tileUrl, {
      minZoom: 2,
      maxZoom: 18,
      attribution: attributionLink(attribution, attributionUrl),
    }).addTo(map);
  }
  // Leaflet measures its container once: a panel that opens or resizes needs a new measure.
  if ("ResizeObserver" in window) {
    new ResizeObserver(() => map.invalidateSize()).observe(element);
  }
  return map;
}

// The picker: a click places the point, the marker can be dragged, the latitude and longitude
// inputs follow both and move the marker when typed, and "Find coordinates" lists candidates.
// `root` carries `data-map-picker` and the ids of the form inputs it works with.
export function initPicker(root) {
  if (!window.L || root.dataset.mapReady === "true") {
    return null;
  }
  const data = root.dataset;
  const input = (name) => {
    const id = data[`${name}Input`];
    return id ? document.getElementById(id) : null;
  };
  const latitudeInput = input("latitude");
  const longitudeInput = input("longitude");
  const sourceInput = input("source");
  // The chosen "Find coordinates" candidate's point, kept when the marker is moved afterwards:
  // the server checks it against the cached lookup and keeps the candidate's metadata with the
  // moved point (spec 15 "Confirmation").
  const pickedLatitudeInput = input("pickedLatitude");
  const pickedLongitudeInput = input("pickedLongitude");
  const remember = (latitude, longitude) => {
    if (pickedLatitudeInput && pickedLongitudeInput) {
      pickedLatitudeInput.value = latitude === null ? "" : latitude.toFixed(PLACES);
      pickedLongitudeInput.value = longitude === null ? "" : longitude.toFixed(PLACES);
    }
  };
  const canvas = root.querySelector("[data-map-canvas]");
  if (!latitudeInput || !longitudeInput || !canvas) {
    return null;
  }
  data.mapReady = "true";
  const status = root.querySelector("[data-map-status]");
  const list = root.querySelector("[data-map-candidates]");
  const findButton = root.querySelector("[data-map-find]");
  const L = window.L;

  const announce = (message) => {
    if (status && message) {
      status.textContent = message;
    }
  };
  const typedPoint = () =>
    validPoint(number(latitudeInput.value), number(longitudeInput.value));

  const start = typedPoint();
  canvas.hidden = false;
  const map = createMap(canvas, {
    lat: start ? start[0] : number(data.defaultLat) ?? 0,
    lng: start ? start[1] : number(data.defaultLng) ?? 0,
    zoom: start ? POINT_ZOOM : number(data.defaultZoom) ?? 4,
    tileUrl: data.tileUrl,
    attribution: data.attribution,
    attributionUrl: data.attributionUrl,
  });

  let marker = null;
  let writing = false; // our own writes must not count as typing

  // Write the point to the inputs and tell the form (Wagtail's unsaved-changes check) about it.
  const write = (latitude, longitude, source) => {
    writing = true;
    try {
      latitudeInput.value = latitude.toFixed(PLACES);
      longitudeInput.value = longitude.toFixed(PLACES);
      if (sourceInput) {
        sourceInput.value = source;
      }
      for (const element of [latitudeInput, longitudeInput, sourceInput]) {
        if (element) {
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
    } finally {
      writing = false;
    }
  };

  const fromMap = (latlng) => {
    const point = latlng.wrap();
    write(point.lat, point.lng, "map");
    announce(data.msgPlaced);
  };

  const place = (latitude, longitude) => {
    if (marker) {
      marker.setLatLng([latitude, longitude]);
      return;
    }
    marker = L.marker([latitude, longitude], {
      draggable: true,
      keyboard: true,
      title: data.msgMarker || "",
      alt: data.msgMarker || "",
      autoPan: true,
    }).addTo(map);
    marker.on("dragend", () => fromMap(marker.getLatLng()));
  };

  const unplace = () => {
    if (marker) {
      marker.remove();
      marker = null;
    }
  };

  if (start) {
    place(start[0], start[1]);
  }

  map.on("click", (event) => {
    place(event.latlng.lat, event.latlng.lng);
    fromMap(event.latlng);
  });

  // Typing: the inputs are the keyboard alternative to clicking and dragging (WCAG 2.5.7).
  const typed = () => {
    if (writing) {
      return;
    }
    const point = typedPoint();
    if (point) {
      place(point[0], point[1]);
      map.setView(point, Math.max(map.getZoom(), POINT_ZOOM), { animate: false });
      if (sourceInput) {
        sourceInput.value = "manual";
      }
      announce(data.msgTyped);
    } else if (latitudeInput.value.trim() === "" && longitudeInput.value.trim() === "") {
      unplace();
      remember(null, null);
      if (sourceInput) {
        sourceInput.value = "";
      }
    }
  };
  latitudeInput.addEventListener("change", typed);
  longitudeInput.addEventListener("change", typed);

  const choose = (latitude, longitude) => {
    place(latitude, longitude);
    map.setView([latitude, longitude], POINT_ZOOM, { animate: false });
    remember(latitude, longitude);
    write(latitude, longitude, "geocoded");
    announce(data.msgChosen);
  };

  const showCandidates = (candidates) => {
    list.replaceChildren();
    for (const candidate of candidates) {
      const point = validPoint(number(candidate.latitude), number(candidate.longitude));
      if (!point) {
        continue;
      }
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-small button-secondary";
      // Provider text: set as text, never as markup.
      button.textContent = candidate.label;
      const detail = document.createElement("span");
      detail.className = "mpmv-map-picker__detail";
      detail.textContent = ` (${candidate.precision}, ${candidate.provider})`;
      button.append(detail);
      button.addEventListener("click", () => choose(point[0], point[1]));
      item.append(button);
      list.append(item);
    }
    list.hidden = list.children.length === 0;
    announce(list.hidden ? data.msgNone : data.msgFound);
  };

  const find = async () => {
    const body = new FormData();
    for (const name of ["street", "postcode", "city", "country"]) {
      const element = input(name);
      body.append(name, element ? element.value : "");
    }
    if (data.language) {
      body.append("language", data.language);
    }
    const form = root.closest("form");
    const token = form ? form.querySelector("input[name=csrfmiddlewaretoken]") : null;
    const headers = token ? { "X-CSRFToken": token.value } : {};
    announce(data.msgSearching);
    findButton.disabled = true;
    try {
      const response = await fetch(data.findUrl, {
        method: "POST",
        body,
        headers,
        credentials: "same-origin",
      });
      const answer = await response.json().catch(() => ({}));
      if (!response.ok) {
        list.hidden = true;
        announce(answer.detail || data.msgError);
        return;
      }
      showCandidates(Array.isArray(answer.candidates) ? answer.candidates : []);
    } catch {
      announce(data.msgError);
    } finally {
      findButton.disabled = false;
    }
  };

  if (findButton && list && data.findUrl) {
    findButton.hidden = false;
    findButton.addEventListener("click", find);
  }
  return map;
}

// Scroll-wheel zoom is off until the map has focus (WCAG 2.1.1 and 2.4.3 in practice: a page
// scroll must not be captured by a map the visitor only passes over). `data-wheel-zoom` shows
// the state to tests and styles.
function wheelZoomOnFocus(map, element) {
  const set = (on) => {
    if (on) {
      map.scrollWheelZoom.enable();
    } else {
      map.scrollWheelZoom.disable();
    }
    element.dataset.wheelZoom = on ? "on" : "off";
  };
  set(false);
  element.addEventListener("focusin", () => set(true));
  element.addEventListener("focusout", (event) => {
    if (!event.relatedTarget || !element.contains(event.relatedTarget)) {
      set(false);
    }
  });
}

// A popup built from text nodes only: event data is never inserted as markup.
function popupContent(lines, link) {
  const box = document.createElement("div");
  lines.forEach((text, index) => {
    if (!text) {
      return;
    }
    const line = document.createElement(index === 0 && !link ? "strong" : "span");
    if (index === 0 && link) {
      const anchor = document.createElement("a");
      anchor.href = link;
      anchor.textContent = text;
      line.append(anchor);
      line.className = "event-map__popup-title";
    } else {
      line.textContent = text;
    }
    if (box.childNodes.length > 0) {
      box.append(document.createElement("br"));
    }
    box.append(line);
  });
  return box;
}

function mapMarker(point, title, content) {
  const marker = window.L.marker(point, { keyboard: true, title, alt: title });
  marker.bindPopup(content, { autoPan: true });
  return marker;
}

function mapOptions(data) {
  return {
    tileUrl: data.tileUrl,
    attribution: data.attribution,
    attributionUrl: data.attributionUrl,
    wheelZoom: false,
  };
}

// The map of an event page (EVT.15): one marker with a popup. The server-rendered fallback
// (the address and the attribution) is replaced by the map, whose attribution control carries
// the same text. The container is the labelled region; the marker is keyboard focusable with
// the venue name as its title.
export function initEventMap(root) {
  if (!window.L || root.dataset.mapReady === "true") {
    return null;
  }
  const data = root.dataset;
  const point = validPoint(number(data.lat), number(data.lng));
  if (!point) {
    return null;
  }
  data.mapReady = "true";
  root.replaceChildren();
  const map = createMap(root, {
    lat: point[0],
    lng: point[1],
    zoom: number(data.zoom) ?? POINT_ZOOM,
    ...mapOptions(data),
  });
  const label = data.label || "";
  mapMarker(point, label, popupContent([label, data.address], "")).addTo(map);
  wheelZoomOnFocus(map, root);
  return map;
}

// The map of the events list (EVT.15): a marker for every listed item that has `data-lat` and
// `data-lng`, with a popup holding its title (a link to the event) and its date line. Items
// without coordinates are listed but not plotted. The container stays hidden when nothing is
// plotted.
export function initIndexMap(root) {
  if (!window.L || root.dataset.mapReady === "true") {
    return null;
  }
  const canvas = root.querySelector("[data-map-canvas]");
  const items = [...document.querySelectorAll("article[data-lat][data-lng]")];
  const plotted = items
    .map((item) => ({
      item,
      point: validPoint(number(item.dataset.lat), number(item.dataset.lng)),
    }))
    .filter((entry) => entry.point);
  if (!canvas || plotted.length === 0) {
    return null;
  }
  const data = root.dataset;
  data.mapReady = "true";
  root.hidden = false;
  const map = createMap(canvas, {
    lat: number(data.defaultLat) ?? 0,
    lng: number(data.defaultLng) ?? 0,
    zoom: number(data.defaultZoom) ?? 4,
    ...mapOptions(data),
  });
  const bounds = window.L.latLngBounds([]);
  for (const { item, point } of plotted) {
    const anchor = item.querySelector("a[href]");
    const title = anchor ? anchor.textContent.trim() : "";
    const meta = item.querySelector(".event-meta");
    const when = meta ? meta.textContent.replace(/\s+/g, " ").trim() : "";
    const href = anchor ? anchor.getAttribute("href") : "";
    mapMarker(point, title, popupContent([title, when], href)).addTo(map);
    bounds.extend(point);
  }
  map.fitBounds(bounds, { maxZoom: 14, padding: [24, 24], animate: false });
  wheelZoomOnFocus(map, canvas);
  return map;
}

function initAll() {
  for (const root of document.querySelectorAll("[data-map-picker]")) {
    initPicker(root);
  }
  for (const root of document.querySelectorAll("[data-event-map]")) {
    initEventMap(root);
  }
  for (const root of document.querySelectorAll("[data-events-map]")) {
    initIndexMap(root);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAll);
} else {
  initAll();
}
