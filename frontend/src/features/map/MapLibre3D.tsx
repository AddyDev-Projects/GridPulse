import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MLMap } from "maplibre-gl";
// MapLibre 6 runs tile parsing in a module worker that it locates relative to its own file, which does
// not survive bundling. Vite bundles the worker (with its shared chunk) and we hand MapLibre the URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

maplibregl.setWorkerUrl(workerUrl);
import { publicApi } from "../../services/api";
import type { LatLng } from "../../types/api";
import type { CityMapProps, MapView } from "./CityMap";
import { CASING_WIDTH, COLORS, POI_GLYPH, ROUTE_DASH, ROUTE_WIDTH, poiTitle } from "./mapTheme";

/**
 * Optional 3D view for the OpenStreetMap provider (never used with Google Maps).
 *
 * Renderer: MapLibre GL JS (WebGL), tilted camera. Basemap: OpenFreeMap's "dark" style, vector tiles
 * in the OpenMapTiles schema built from OpenStreetMap; no API key. Buildings are extruded from the OSM
 * `building` layer using `render_height` / `render_min_height`. Where OSM has no height or levels tag,
 * the tile schema supplies a default height, so heights are approximate. No terrain (Mangaluru's mapped
 * area is close to sea level; a DEM source was not added). GridPulse overlays come from the same
 * backend geometry as the 2D map.
 */
const STYLE_URL = "https://tiles.openfreemap.org/styles/dark";
const ATTRIBUTION = '<a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a> · © OpenMapTiles · © OpenStreetMap contributors';
const LOAD_TIMEOUT_MS = 15_000;
// Leaflet zoom levels assume 256 px tiles; MapLibre's assume 512 px. Same view = one level lower.
const toML = (leafletZoom: number) => leafletZoom - 1;
const toLeaflet = (mlZoom: number) => Math.round(mlZoom + 1);

type FC = GeoJSON.FeatureCollection;
const ll = ([lat, lon]: LatLng): [number, number] => [lon, lat];
const line = (coords: LatLng[], props: Record<string, unknown> = {}): GeoJSON.Feature =>
  ({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: coords.map(ll) } });
const point = (lat: number, lon: number, props: Record<string, unknown> = {}): GeoJSON.Feature =>
  ({ type: "Feature", properties: props, geometry: { type: "Point", coordinates: [lon, lat] } });
const fc = (features: GeoJSON.Feature[]): FC => ({ type: "FeatureCollection", features });
const box = ([s, w, n, e]: number[]): GeoJSON.Position[] => [[w, s], [e, s], [e, n], [w, n], [w, s]];

function webglSupported(): boolean {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

function overlayData(p: CityMapProps): Record<string, FC> {
  const [s, w, n, e] = p.city.coverage_bbox;
  return {
    "gp-coverage": fc([{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: box([s, w, n, e]) } }]),
    "gp-zones": fc((p.zones ?? []).map((z) => ({ type: "Feature", properties: { title: `${z.name}: ${z.status}. SIMULATED feeder zone.`,
      color: z.status === "energized" ? COLORS.zone : COLORS.closed }, geometry: { type: "Polygon", coordinates: [box(z.bounds)] } }))),
    "gp-hazard": fc((p.hazard ?? []).map((g) => line(g))),
    "gp-closures": fc((p.closures ?? []).map((c) => line(c.geometry, {
      title: `${c.name}: ${c.kind} (simulated)`, color: c.kind === "closed" ? COLORS.closed : COLORS.caution }))),
    "gp-routes": fc((p.routes ?? []).map((r) => line(r.geometry, { title: r.label, color: COLORS[r.tone], tone: r.tone,
      alternative: Boolean(r.alternative) }))),
    "gp-pois": fc((p.pois ?? []).map((x) => point(x.lat, x.lon, { title: poiTitle(x), color: COLORS.poi[x.kind] ?? "#c9c5d6",
      glyph: x.kind === "hospital" ? "" : POI_GLYPH[x.kind], radius: x.kind === "hospital" ? 5 : 8 }))),
    "gp-signals": fc((p.signals ?? []).map((sg) => point(sg.lat, sg.lon, { title: `${sg.name}: ${sg.status}. OSM location; power simulated.`,
      color: sg.status === "operational" ? COLORS.signalOk : COLORS.signalDark }))),
    "gp-markers": fc([
      ...(p.markers ?? []).map((m) => point(m.lat, m.lon, { id: m.id, title: m.detail ? `${m.label}: ${m.detail}` : m.label,
        color: m.color ?? COLORS.incident, radius: m.radius ?? 9 })),
      ...(p.picked ? [point(p.picked[0], p.picked[1], { id: "__picked", title: "Reported location", color: COLORS.incident, radius: 11 })] : []),
    ]),
  };
}

/**
 * Why the 3D view looked almost black: OpenFreeMap's "dark" style is near-black by design (background
 * rgb(12,12,12), minor roads #181818, major roads 7 % grey, labels ~40 % grey) and defines no light, so
 * buildings were shaded flat. Instead of a CSS brightness filter, the basemap layers are recoloured at
 * load to the GridPulse palette (close to the 2D map's darkened OSM tiles) and an explicit light is set.
 * Layers are matched by id, then by type and source-layer, so a renamed layer degrades gracefully.
 */
const PALETTE: Record<string, Record<string, unknown>> = {
  background: { "background-color": "#1d1a27" },
  water: { "fill-color": "#1c2b42" },
  waterway: { "line-color": "#2a3f5e" },
  landuse_residential: { "fill-color": "#24212f" },
  landcover_wood: { "fill-color": "#1f2b27" },
  landuse_park: { "fill-color": "#20302a" },
  building: { "fill-color": "#2e2a3c", "fill-outline-color": "#3b364b" },
  highway_path: { "line-color": "#3d384b" },
  highway_minor: { "line-color": "#4d4760" },
  highway_major_casing: { "line-color": "#1a1722" },
  highway_major_inner: { "line-color": "#736c8c" },
  highway_major_subtle: { "line-color": "#5a5470" },
  highway_motorway_casing: { "line-color": "#1a1722" },
  highway_motorway_inner: { "line-color": "#8f88ad" },
  highway_motorway_subtle: { "line-color": "#5a5470" },
  railway: { "line-color": "#4d4760" },
  railway_minor: { "line-color": "#4d4760" },
  railway_transit: { "line-color": "#4d4760" },
};
const LABEL = { "text-color": "#ddd8ea", "text-halo-color": "#12101a", "text-halo-width": 1.2 };

function brightenBasemap(map: MLMap) {
  const set = (id: string, paint: Record<string, unknown>) => {
    for (const [k, v] of Object.entries(paint)) {
      try {
        map.setPaintProperty(id, k as Parameters<MLMap["setPaintProperty"]>[1], v as Parameters<MLMap["setPaintProperty"]>[2]);
      } catch {
        /* property not valid for this layer type: leave the style's own value */
      }
    }
  };
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.id.startsWith("gp-")) continue;
    const sl = (layer as { "source-layer"?: string })["source-layer"];
    if (PALETTE[layer.id]) set(layer.id, PALETTE[layer.id]);
    else if (layer.type === "symbol") set(layer.id, layer.id === "water_name" ? { ...LABEL, "text-color": "#9dbbe0" } : LABEL);
    else if (layer.type === "line" && sl === "transportation") set(layer.id, { "line-color": "#4d4760" });
    else if (layer.type === "line" && sl === "boundary") set(layer.id, { "line-color": "#57516a" });
  }
  // Directional light for the extruded buildings (MapLibre's default leaves them flat and dark).
  map.setLight({ anchor: "viewport", color: "#ffffff", intensity: 0.45, position: [1.4, 210, 35] });
}

function addLayers(map: MLMap, data: Record<string, FC>) {
  brightenBasemap(map);
  // Extruded OSM buildings beneath the GridPulse overlays, if the style provides the OpenMapTiles source.
  if (map.getSource("openmaptiles") && !map.getLayer("gp-buildings-3d")) {
    const firstSymbol = map.getStyle().layers?.find((l) => l.type === "symbol")?.id;
    map.addLayer({
      id: "gp-buildings-3d", type: "fill-extrusion", source: "openmaptiles", "source-layer": "building", minzoom: 14,
      paint: {
        "fill-extrusion-color": "#8a81a8",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 0],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        // Slightly translucent so streets and GridPulse overlays behind tall blocks stay readable.
        "fill-extrusion-opacity": 0.78,
        "fill-extrusion-vertical-gradient": true,
      },
    }, firstSymbol);
  }
  for (const [id, fcData] of Object.entries(data)) map.addSource(id, { type: "geojson", data: fcData });
  map.addSource("gp-graph", { type: "geojson", data: fc([]) });
  map.addLayer({ id: "gp-coverage", type: "line", source: "gp-coverage", paint: { "line-color": "#b4a2fb", "line-width": 1, "line-dasharray": [3, 3], "line-opacity": 0.6 } });
  map.addLayer({ id: "gp-zones", type: "line", source: "gp-zones", paint: { "line-color": ["get", "color"], "line-width": 1, "line-dasharray": [1, 3] } });
  map.addLayer({ id: "gp-graph", type: "line", source: "gp-graph", paint: { "line-color": "#cfc2ff", "line-width": 1.5, "line-opacity": 0.55 } });
  map.addLayer({ id: "gp-hazard", type: "line", source: "gp-hazard", paint: { "line-color": COLORS.hazard, "line-width": 9, "line-opacity": 0.35 } });
  map.addLayer({ id: "gp-closures", type: "line", source: "gp-closures", paint: { "line-color": ["get", "color"], "line-width": 5, "line-dasharray": [2, 1.5] } });
  // Same patterns as 2D (ROUTE_DASH): MapLibre dashes are in line widths, so the px patterns are divided by the width.
  map.addLayer({ id: "gp-route-alternatives", type: "line", source: "gp-routes", filter: ["get", "alternative"],
    paint: { "line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.6, "line-dasharray": [4 / 3, 8 / 3] } });
  map.addLayer({ id: "gp-route-casing", type: "line", source: "gp-routes", filter: ["!", ["get", "alternative"]],
    layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": COLORS.casing, "line-width": CASING_WIDTH, "line-opacity": 0.85 } });
  for (const tone of ["route", "routePolice", "routeAlt", "conflict"] as const) {
    const dash = ROUTE_DASH[tone]?.split(" ").map((n) => Number(n) / ROUTE_WIDTH);
    map.addLayer({ id: `gp-routes-${tone}`, type: "line", source: "gp-routes",
      filter: ["all", ["!", ["get", "alternative"]], ["==", ["get", "tone"], tone]],
      layout: dash ? {} : { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ROUTE_WIDTH, ...(dash ? { "line-dasharray": dash } : {}) } });
  }
  map.addLayer({ id: "gp-pois", type: "circle", source: "gp-pois", paint: { "circle-color": ["get", "color"], "circle-radius": ["get", "radius"],
    "circle-stroke-color": COLORS.casing, "circle-stroke-width": 1.5 } });
  map.addLayer({ id: "gp-poi-glyphs", type: "symbol", source: "gp-pois",
    layout: { "text-field": ["get", "glyph"], "text-size": 10, "text-font": ["Noto Sans Regular"], "text-allow-overlap": true },
    paint: { "text-color": COLORS.casing } });
  map.addLayer({ id: "gp-signals", type: "circle", source: "gp-signals", paint: { "circle-color": ["get", "color"], "circle-radius": 6,
    "circle-stroke-color": COLORS.casing, "circle-stroke-width": 2 } });
  map.addLayer({ id: "gp-markers", type: "circle", source: "gp-markers", paint: { "circle-color": ["get", "color"], "circle-radius": ["get", "radius"],
    "circle-opacity": 0.9, "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
}

const HOVER_LAYERS = ["gp-markers", "gp-pois", "gp-signals", "gp-routes-route", "gp-routes-routePolice", "gp-routes-routeAlt",
  "gp-routes-conflict", "gp-route-alternatives", "gp-closures", "gp-zones"];

export default function MapLibre3D(props: CityMapProps & {
  initialView?: MapView | null;
  onView: (v: MapView) => void;
  onFailure: (reason: string) => void;
}) {
  const { city, focus, showGraph = false, initialView, onView, onFailure } = props;
  const root = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const latest = useRef(props);
  const [loaded, setLoaded] = useState(false);
  const firstFit = useRef(true);

  useEffect(() => { latest.current = props; });

  useEffect(() => {
    if (!root.current) return;
    if (!webglSupported()) {
      onFailure("WebGL is not available in this browser");
      return;
    }
    const [s, w, n, e] = city.coverage_bbox;
    const start = initialView ?? { center: city.center as LatLng, zoom: 14 };
    let map: MLMap;
    try {
      map = new maplibregl.Map({
        container: root.current, style: STYLE_URL, center: ll(start.center), zoom: toML(start.zoom), pitch: 60, bearing: -17,
        maxPitch: 75, minZoom: 11, maxZoom: 19, maxBounds: [[w - 0.06, s - 0.06], [e + 0.06, n + 0.06]],
        attributionControl: { compact: true, customAttribution: ATTRIBUTION },
      });
    } catch (err) {
      onFailure(err instanceof Error ? err.message : "the 3D renderer could not start");
      return;
    }
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");
    let ready = false;
    const timeout = window.setTimeout(() => { if (!ready) onFailure("the 3D basemap did not load in time"); }, LOAD_TIMEOUT_MS);
    map.on("error", (ev) => {
      // Before the style loads, any error means there is no usable 3D view; later tile errors are tolerated.
      if (!ready) onFailure(ev.error?.message ? `basemap error: ${ev.error.message.slice(0, 80)}` : "the 3D basemap failed to load");
    });
    map.on("load", () => {
      ready = true;
      window.clearTimeout(timeout);
      addLayers(map, overlayData(latest.current));
      setLoaded(true);
    });
    map.on("moveend", () => {
      const c = map.getCenter();
      onView({ center: [c.lat, c.lng], zoom: toLeaflet(map.getZoom()) });
    });
    map.on("click", (ev) => {
      const hit = map.getLayer("gp-markers") ? map.queryRenderedFeatures(ev.point, { layers: ["gp-markers"] })[0] : undefined;
      const id = hit?.properties?.id as string | undefined;
      const marker = id ? latest.current.markers?.find((m) => m.id === id) : undefined;
      if (marker?.onClick) return marker.onClick();
      latest.current.onPick?.([Number(ev.lngLat.lat.toFixed(6)), Number(ev.lngLat.lng.toFixed(6))]);
    });
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "gp-ml-popup" });
    map.on("mousemove", (ev) => {
      const layers = HOVER_LAYERS.filter((l) => map.getLayer(l));
      const f = layers.length ? map.queryRenderedFeatures(ev.point, { layers })[0] : undefined;
      map.getCanvas().style.cursor = f ? "pointer" : latest.current.onPick ? "crosshair" : "";
      if (f?.properties?.title) popup.setLngLat(ev.lngLat).setText(String(f.properties.title)).addTo(map);
      else popup.remove();
    });
    return () => {
      window.clearTimeout(timeout);
      popup.remove();
      map.remove();
      mapRef.current = null;
    };
    // One map per mount; overlay changes are pushed into the sources below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Push overlay changes into the existing sources.
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    for (const [id, data] of Object.entries(overlayData(props))) (map.getSource(id) as GeoJSONSource | undefined)?.setData(data);
  }, [loaded, props]);

  // Same focus rules as 2D; the first fit is skipped when restoring the user's view from 2D.
  const focusKey = focus ? `${focus.length}:${focus[0]?.join(",")}:${focus[focus.length - 1]?.join(",")}` : "none";
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    if (firstFit.current && initialView) {
      firstFit.current = false;
      return;
    }
    firstFit.current = false;
    if (focus && focus.length > 1) {
      const lats = focus.map((p) => p[0]);
      const lons = focus.map((p) => p[1]);
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 48, maxZoom: 16, pitch: 60 });
    } else if (focus && focus.length === 1) {
      map.easeTo({ center: ll(focus[0]), zoom: 15.5 });
    } else if (!initialView) {
      const [s, w, n, e] = city.coverage_bbox;
      map.fitBounds([[w, s], [e, n]], { padding: 24, pitch: 60 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, focusKey]);

  // The router's own road segments, when asked for and zoomed in (as in 2D).
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map || !showGraph) return;
    let timer: number | undefined;
    const load = () => {
      const src = map.getSource("gp-graph") as GeoJSONSource | undefined;
      if (!src) return;
      if (toLeaflet(map.getZoom()) < 15) return src.setData(fc([]));
      const b = map.getBounds();
      publicApi.segments(b.getSouth(), b.getWest(), b.getNorth(), b.getEast())
        .then((r) => src.setData(fc(r.segments.map((seg) => line(seg.geometry)))))
        .catch(() => src.setData(fc([])));
    };
    const schedule = () => { window.clearTimeout(timer); timer = window.setTimeout(load, 250); };
    map.on("moveend", schedule);
    load();
    return () => { window.clearTimeout(timer); map.off("moveend", schedule); };
  }, [loaded, showGraph]);

  return (
    <div className="relative h-full w-full">
      <div ref={root} className="h-full w-full" aria-label="3D map (MapLibre, OpenStreetMap data)" />
      {!loaded && <div className="absolute inset-0 grid place-items-center bg-ink-900/80 text-xs text-fog-400">Loading 3D map…</div>}
      {loaded && (
        <p className="pointer-events-none absolute bottom-6 left-2 rounded bg-ink-950/80 px-2 py-0.5 text-[10px] text-fog-400">
          3D buildings from OSM; heights approximate where untagged. Drag with right mouse or two fingers to tilt and rotate.
        </p>
      )}
    </div>
  );
}
