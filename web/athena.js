/* Shared helpers for the Athena pages: formatting, risk bands, icons and the map setup. */
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const ICON = {
  walk:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="13" cy="4" r="2"/><path d="M10.5 8.5 8 20h2.2l1.6-7 2.2 2.2V20h2v-6.2l-2.2-2.2.7-3.2A6 6 0 0 0 19 11V9a4 4 0 0 1-3.4-1.9l-1-1.6A2 2 0 0 0 12.9 5c-.3 0-.6 0-.9.2L7 7.3V12h2V8.6z"/></svg>',
  bike:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="6" cy="17" r="3.5"/><circle cx="18" cy="17" r="3.5"/><path d="M6 17l4-8h5l3 8M10 9l2 8M14 5h3"/></svg>',
  transit:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 11h14M8 21l2-4M16 21l-2-4"/></svg>',
  police:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z"/></svg>',
  hospital:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/></svg>',
  station:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><rect x="6" y="3" width="12" height="13" rx="3"/><path d="M6 10h12M9 21l2-5M15 21l-2-5"/></svg>',
  clock:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  info:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>',
  back:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
  next:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>',
  swap:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4"/></svg>',
  lamp:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M9 21h6M10 17h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg>',
};
const HELP = {police: "Police station", hospital: "Hospital", station: "Train station"};
const RISK_TEXT = {low: "Lower risk indicators", medium: "Moderate risk indicators", high: "Higher risk indicators", unknown: "Insufficient lighting data"};
const RISK_SHORT = {low: "Lower risk", medium: "Moderate risk", high: "Higher risk", unknown: "Not enough data"};

// Validated band thresholds from the Athena street-tagging pipeline.
const riskBand = r => r == null ? "unknown" : r < .33 ? "low" : r < .55 ? "medium" : "high";
const riskText = r => RISK_TEXT[riskBand(r)];
const riskColor = r => ({low: css("--ok"), medium: css("--warn"), high: css("--bad"), unknown: css("--route-dim")})[riskBand(r)];
const litText = l => l === "yes" ? "Lit" : l === "no" ? "Unlit" : "No lighting data";

// Times arrive as Sydney ISO strings ("2026-10-20T22:00:00+11:00"); show the clock time as given.
function clock(iso){
  if(!iso) return "";
  return hhmmText(iso.slice(11, 16));
}
function hhmmText(hhmm){
  const [h, m] = hhmm.split(":").map(Number);
  return `${(h % 12) || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
}
const mins = s => Math.max(1, Math.round(s / 60));
const km = m => m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 10) * 10} m`;
function duration(min){
  min = Math.round(min);
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} hr ${min % 60 ? `${min % 60} min` : ""}`.trim();
}
// Current Sydney time as HH:MM, rounded up to the next 5 minutes.
function sydneyNow(){
  const p = new Intl.DateTimeFormat("en-GB", {hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Australia/Sydney"}).format(new Date());
  let [h, m] = p.split(":").map(Number);
  m = Math.ceil(m / 5) * 5; if(m === 60){ m = 0; h = (h + 1) % 24; }
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}
// Which of Athena's street-risk profiles a departure time uses (mirrors athena/journeys.py band_for_time).
function bandForTime(hhmm){
  const [h, m] = hhmm.split(":").map(Number), t = h * 60 + m;
  return t >= 300 && t < 1200 ? "18" : t >= 1200 && t < 1410 ? "22" : "01";
}
const BAND_LABEL = {"18": "6 pm", "22": "10 pm", "01": "1 am"};

// Google encoded polyline, precision 5 (what OpenTripPlanner returns in legGeometry.points).
function decodePolyline(str){
  let i = 0, lat = 0, lon = 0; const out = [];
  while(i < str.length){
    for(const k of [0, 1]){
      let b, shift = 0, res = 0;
      do { b = str.charCodeAt(i++) - 63; res |= (b & 31) << shift; shift += 5; } while(b >= 32);
      const d = (res & 1) ? ~(res >> 1) : (res >> 1);
      if(k === 0) lat += d; else lon += d;
    }
    out.push([lon / 1e5, lat / 1e5]);
  }
  return out;
}
function haversine(a, b){
  const R = 6371000, toR = x => x * Math.PI / 180;
  const dLat = toR(b[1] - a[1]), dLon = toR(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a[1])) * Math.cos(toR(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
async function getJSON(url, ms = 15000){
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(url, {signal: ctl.signal});
    const body = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
    return body;
  } finally { clearTimeout(t); }
}

/* ---------- map ---------- */
// Starts on a plain background so Athena's layers render even if basemap tiles are blocked;
// the OpenFreeMap basemap is slotted in underneath afterwards.
function createMap(container, {center = [151.218, -33.895], zoom = 12.4, noteEl} = {}){
  const map = new maplibregl.Map({
    container, center, zoom, attributionControl: false, dragRotate: false, pitchWithRotate: false,
    style: {version: 8, sources: {}, layers: [{id: "bg", type: "background", paint: {"background-color": css("--map-bg")}}]},
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({showCompass: false}), "top-right");
  map.addControl(new maplibregl.GeolocateControl({positionOptions: {enableHighAccuracy: true}}), "top-right");
  map.addControl(new maplibregl.AttributionControl({compact: true,
    customAttribution: 'Routes: OpenTripPlanner · Street data: Athena / Zetaris · <a href="https://openfreemap.org">OpenFreeMap</a> <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>'}));
  const ready = new Promise(r => map.on("load", r));
  ready.then(() => {
    document.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
    addBasemap(map, noteEl);
  });
  return {map, ready};
}
async function addBasemap(map, noteEl){
  const dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light";
  const urls = dark ? ["https://tiles.openfreemap.org/styles/dark", "https://tiles.openfreemap.org/styles/positron"]
                    : ["https://tiles.openfreemap.org/styles/positron"];
  for(const url of urls){
    try {
      const style = await getJSON(url, 6000);
      if(style.glyphs) map.setGlyphs(style.glyphs);
      if(typeof style.sprite === "string") map.setSprite(style.sprite);
      for(const [id, src] of Object.entries(style.sources || {})) if(!map.getSource(id)) map.addSource(id, src);
      const before = map.getStyle().layers.find(l => l.id !== "bg")?.id;
      for(const layer of style.layers || []){
        if(layer.type === "background") continue;
        try { map.addLayer(layer, before); } catch(e) { /* skip a layer this build can't parse */ }
      }
      return true;
    } catch(e) { /* try the next style, then keep the plain background */ }
  }
  if(noteEl){ noteEl.hidden = false; noteEl.textContent = "Basemap unavailable, showing a plain background"; }
  return false;
}
// Street risk overlay: one GeoJSON line layer coloured by the street's risk score.
function addSafetyLayer(map){
  map.addSource("safety", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addLayer({id: "safety-line", type: "line", source: "safety", layout: {"line-cap": "round", "line-join": "round"},
    paint: {"line-width": ["interpolate", ["linear"], ["zoom"], 12, 3, 16, 9], "line-opacity": .6,
      "line-color": ["case", ["==", ["get", "risk"], null], css("--route-dim"),
        ["<", ["get", "risk"], .33], css("--ok"), ["<", ["get", "risk"], .55], css("--warn"), css("--bad")]}});
}
function streetPopupHTML(p){
  const r = p.risk === "null" || p.risk == null ? null : Number(p.risk);
  const bits = [litText(p.lit), p.highway ? String(p.highway).replace(/_/g, " ") : "", p.tunnel === true || p.tunnel === "true" ? "tunnel" : ""].filter(Boolean);
  return `<b>${esc(p.name)}</b><br><span style="color:${riskColor(r)};font-weight:600">${riskText(r)}</span>${r != null ? ` · ${r.toFixed(2)}` : ""}<br><span style="color:${css("--muted")}">${esc(bits.join(" · "))}</span>`;
}
// Help locations (OpenStreetMap): police stations, hospitals and train stations.
async function loadHelp(){
  try { return await getJSON("/api/safety-points"); } catch(e) { return {points: [], available: false}; }
}
function helpMarkers(points){
  return points.filter(p => HELP[p.category]).map(p => {
    const el = document.createElement("div"); el.className = "pin " + p.category; el.innerHTML = ICON[p.category];
    el.setAttribute("aria-label", `${HELP[p.category]}: ${p.name || ""}`);
    const extra = [p.phone ? `<a href="tel:${esc(p.phone)}">${esc(p.phone)}</a>` : "",
                   p.emergency_department ? "Emergency department" : "", p.opening_hours ? esc(p.opening_hours) : ""].filter(Boolean).join("<br>");
    const m = new maplibregl.Marker({element: el}).setLngLat([p.lon, p.lat])
      .setPopup(new maplibregl.Popup({offset: 14}).setHTML(`<b>${esc(p.name || HELP[p.category])}</b><br>${HELP[p.category]}${extra ? "<br>" + extra : ""}`));
    m.helpId = p.id;
    return m;
  });
}
function fitPoints(map, pts, padding){
  if(!pts.length) return false;
  const b = pts.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
  map.fitBounds(b, {padding, maxZoom: 16, duration: reduceMotion ? 0 : 600});
  return true;
}
