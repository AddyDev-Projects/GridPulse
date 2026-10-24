import "leaflet/dist/leaflet.css";
import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import L from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Marker,
  Pane,
  Polyline,
  Rectangle,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type { LatLngBoundsExpression, LatLngExpression } from "leaflet";
import { publicApi } from "../../services/api";
import type { City, Closure, LatLng, OverlayNode, Poi, RoadSegment, Zone } from "../../types/api";
import { cx } from "../../components/ui";
import { GoogleMapCanvas } from "./GoogleMap";
import { ALT_DASH, CASING_WIDTH, COLORS, POI_GLYPH, ROUTE_DASH, ROUTE_WIDTH, poiTitle, type RouteTone } from "./mapTheme";

export { COLORS, SERVICE_LEGEND, routeTone } from "./mapTheme";

// Loaded only when someone switches to 3D, so the default 2D map does not pay for WebGL.
const MapLibre3D = lazy(() => import("./MapLibre3D"));

// Standard OpenStreetMap tiles (tile usage policy: light use, attribution required). Darkened with CSS to fit the theme.
export const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export interface MapMarker {
  id: string;
  lat: number;
  lon: number;
  label: string;
  detail?: string;
  color?: string;
  radius?: number;
  onClick?: () => void;
}

export interface MapRoute {
  id: string;
  geometry: LatLng[];
  label: string;
  tone: RouteTone;
  /** An alternative candidate the traffic agent evaluated but did not select: drawn thin and translucent. */
  alternative?: boolean;
}

/** Centre and Leaflet zoom shared by the 2D and 3D views, so switching keeps the user's place. */
export interface MapView {
  center: LatLng;
  zoom: number;
}

export interface CityMapProps {
  city: City;
  className?: string;
  markers?: MapMarker[];
  closures?: Closure[];
  routes?: MapRoute[];
  hazard?: LatLng[][];
  signals?: OverlayNode[];
  zones?: Zone[];
  pois?: Poi[];
  showGraph?: boolean;
  focus?: LatLng[] | null;
  picked?: LatLng | null;
  onPick?: (p: LatLng) => void;
  ariaLabel?: string;
}

function FitTo({ points, fallback, skipFirst }: { points: LatLng[] | null | undefined; fallback: LatLngBoundsExpression; skipFirst?: boolean }) {
  const map = useMap();
  const first = useRef(true);
  const key = points ? `${points.length}:${points[0]?.join(",")}:${points[points.length - 1]?.join(",")}` : "none";
  useEffect(() => {
    // When the user switched from 3D, keep the view they had instead of re-fitting on mount.
    if (first.current && skipFirst) {
      first.current = false;
      return;
    }
    first.current = false;
    if (points && points.length > 1) map.fitBounds(points as LatLngBoundsExpression, { padding: [36, 36], maxZoom: 17 });
    else if (points && points.length === 1) map.setView(points[0] as LatLngExpression, 16);
    else map.fitBounds(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

function ViewTracker({ onView }: { onView: (v: MapView) => void }) {
  const map = useMapEvents({
    moveend: () => {
      const c = map.getCenter();
      onView({ center: [c.lat, c.lng], zoom: map.getZoom() });
    },
  });
  return null;
}

const poiIcons = new Map<string, L.DivIcon>();
function poiIcon(kind: Poi["kind"]) {
  let icon = poiIcons.get(kind);
  if (!icon) {
    icon = L.divIcon({
      className: "gp-poi-icon",
      html: `<span class="gp-poi gp-poi-${kind}" aria-hidden="true">${POI_GLYPH[kind]}</span>`,
      iconSize: kind === "hospital" ? [14, 14] : [18, 18],
      iconAnchor: kind === "hospital" ? [7, 7] : [9, 9],
    });
    poiIcons.set(kind, icon);
  }
  return icon;
}

function PickHandler({ onPick }: { onPick: (p: LatLng) => void }) {
  useMapEvents({ click: (e) => onPick([Number(e.latlng.lat.toFixed(6)), Number(e.latlng.lng.toFixed(6))]) });
  return null;
}

/** Fetches the router's own road segments for the visible area once zoomed in, so what is drawn is the graph. */
function GraphLayer() {
  const [segments, setSegments] = useState<RoadSegment[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const map = useMapEvents({
    moveend: () => schedule(),
  });
  const schedule = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 250);
  };
  const load = () => {
    if (map.getZoom() < 15) {
      setSegments([]);
      setNote("Zoom in to see the routing graph");
      return;
    }
    const b = map.getBounds();
    publicApi
      .segments(b.getSouth(), b.getWest(), b.getNorth(), b.getEast())
      .then((r) => {
        setSegments(r.segments);
        setNote(r.truncated ? "Showing the first 3,000 segments" : null);
      })
      .catch(() => setNote("Could not load road graph segments"));
  };
  useEffect(() => {
    load();
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      {segments.map((s) => (
        <Polyline key={s.id} positions={s.geometry} pathOptions={{ color: "#cfc2ff", weight: 1.5, opacity: 0.55 }}>
          <Tooltip sticky>{`${s.name} (${s.id})${s.oneway ? ", one-way" : ""}`}</Tooltip>
        </Polyline>
      ))}
      {note && (
        <div className="leaflet-top leaflet-left" style={{ marginTop: 80 }}>
          <div className="leaflet-control rounded-md bg-ink-900/90 px-2 py-1 text-[11px] text-fog-300">{note}</div>
        </div>
      )}
    </>
  );
}

function LeafletMap({
  city, markers = [], closures = [], routes = [], hazard = [], signals = [], zones = [], pois = [],
  showGraph = false, focus, picked, onPick, initialView, onView,
}: CityMapProps & { initialView?: MapView | null; onView?: (v: MapView) => void }) {
  const [s, w, n, e] = city.coverage_bbox;
  const coverage: LatLngBoundsExpression = useMemo(() => [[s, w], [n, e]], [s, w, n, e]);
  const start = initialView ? { center: initialView.center as LatLngExpression, zoom: initialView.zoom } : { bounds: coverage };
  return (
      <MapContainer {...start} maxBounds={[[s - 0.05, w - 0.05], [n + 0.05, e + 0.05]]} minZoom={12} maxZoom={19}
        className="h-full w-full bg-ink-900" scrollWheelZoom style={{ cursor: onPick ? "crosshair" : undefined }}>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} maxZoom={19} className="gp-dark-tiles" />
        <Rectangle bounds={coverage} pathOptions={{ color: "#b4a2fb", weight: 1, dashArray: "6 6", fill: false, opacity: 0.6 }}>
          <Tooltip sticky>GridPulse coverage area (mapped road graph)</Tooltip>
        </Rectangle>
        {zones.map((z) => (
          <Rectangle key={z.id} bounds={[[z.bounds[0], z.bounds[1]], [z.bounds[2], z.bounds[3]]]}
            pathOptions={{ color: z.status === "energized" ? COLORS.zone : COLORS.closed, weight: 1, fillOpacity: z.status === "energized" ? 0.03 : 0.12, dashArray: "2 6" }}>
            <Tooltip sticky>{`${z.name}: ${z.status}. SIMULATED feeder zone, not a real substation.`}</Tooltip>
          </Rectangle>
        ))}
        {showGraph && <GraphLayer />}
        {hazard.map((g, i) => (
          <Polyline key={`hz${i}`} positions={g} pathOptions={{ color: COLORS.hazard, weight: 9, opacity: 0.35 }} />
        ))}
        {closures.map((c) => (
          <Polyline key={`cl-${c.segment_id}`} positions={c.geometry}
            pathOptions={{ color: c.kind === "closed" ? COLORS.closed : COLORS.caution, weight: 5, dashArray: "8 6" }}>
            <Tooltip sticky>{`${c.name}: ${c.kind}${c.source === "scenario" ? " (simulated scenario)" : " (simulated, approved plan)"}`}</Tooltip>
          </Polyline>
        ))}
        {routes.filter((r) => r.alternative).map((r) => (
          <Polyline key={r.id} positions={r.geometry}
            pathOptions={{ color: COLORS[r.tone], weight: 3, opacity: 0.6, dashArray: ALT_DASH, lineCap: "round" }}>
            <Tooltip sticky>{r.label}</Tooltip>
          </Polyline>
        ))}
        {routes.filter((r) => !r.alternative).map((r) => (
          <Polyline key={`${r.id}-casing`} positions={r.geometry} interactive={false} className="gp-route-casing"
            pathOptions={{ color: COLORS.casing, weight: CASING_WIDTH, opacity: 0.85, lineCap: "round", lineJoin: "round" }} />
        ))}
        {routes.filter((r) => !r.alternative).map((r) => (
          <Polyline key={r.id} positions={r.geometry}
            pathOptions={{ color: COLORS[r.tone], weight: ROUTE_WIDTH, opacity: 1, dashArray: ROUTE_DASH[r.tone],
              lineCap: "round", lineJoin: "round" }}>
            <Tooltip sticky>{r.label}</Tooltip>
          </Polyline>
        ))}
        {/* Stations sit in a pane below routes and incident markers so they never hide them. */}
        <Pane name="gp-pois" style={{ zIndex: 390 }}>
          {pois.map((p) => (
            <Marker key={p.id} position={[p.lat, p.lon]} icon={poiIcon(p.kind)} pane="gp-pois" keyboard={false}
              alt={poiTitle(p)}>
              <Tooltip>{poiTitle(p)}</Tooltip>
            </Marker>
          ))}
        </Pane>
        {signals.map((sg) => (
          <CircleMarker key={sg.id} center={[sg.lat, sg.lon]} radius={6}
            pathOptions={{ color: sg.status === "operational" ? COLORS.signalOk : COLORS.signalDark, fillOpacity: 0.9, weight: 2 }}>
            <Tooltip>{`${sg.name}: ${sg.status}. Location from OSM; power model simulated.`}</Tooltip>
          </CircleMarker>
        ))}
        {markers.map((m) => (
          <CircleMarker key={m.id} center={[m.lat, m.lon]} radius={m.radius ?? 9}
            eventHandlers={m.onClick ? { click: m.onClick } : undefined}
            pathOptions={{ color: "#ffffff", fillColor: m.color ?? COLORS.incident, fillOpacity: 0.95, weight: 2.5 }}>
            <Tooltip>{m.detail ? `${m.label}: ${m.detail}` : m.label}</Tooltip>
          </CircleMarker>
        ))}
        {picked && (
          <CircleMarker center={picked} radius={11} pathOptions={{ color: "#ffffff", fillColor: COLORS.incident, fillOpacity: 0.9, weight: 3 }}>
            <Tooltip permanent direction="top" offset={[0, -10]}>Reported location</Tooltip>
          </CircleMarker>
        )}
        {onPick && <PickHandler onPick={onPick} />}
        {onView && <ViewTracker onView={onView} />}
        <FitTo points={focus} fallback={coverage} skipFirst={Boolean(initialView)} />
      </MapContainer>
  );
}

/** If the 3D bundle itself fails to load (offline, blocked CDN), fall back to 2D instead of a blank map. */
class ThreeDBoundary extends Component<{ onError: (reason: string) => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    this.props.onError(error instanceof Error ? error.message.slice(0, 80) : "the 3D renderer failed");
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

const GOOGLE_MAPS_KEY = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined)?.trim();
// Existing configuration decides the default; the operator's choice then holds for this browser session.
const GOOGLE_TRAFFIC_DEFAULT = import.meta.env.VITE_GOOGLE_MAPS_TRAFFIC_LAYER === "true";
const TRAFFIC_PREF = "gridpulse:google-traffic";

function readTrafficPref(): boolean {
  try {
    const v = window.sessionStorage.getItem(TRAFFIC_PREF);
    return v === null ? GOOGLE_TRAFFIC_DEFAULT : v === "1";
  } catch {
    return GOOGLE_TRAFFIC_DEFAULT;
  }
}

function writeTrafficPref(on: boolean) {
  try {
    window.sessionStorage.setItem(TRAFFIC_PREF, on ? "1" : "0");
  } catch {
    /* storage unavailable (private mode): the toggle still works for this page */
  }
}

const toggleCls = (on: boolean) =>
  cx("inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[11px] ring-1 ring-inset transition focus-visible:outline-2 focus-visible:outline-lilac-300",
    on ? "bg-lilac-300/15 text-lilac-100 ring-lilac-300/40" : "bg-ink-950/85 text-fog-300 ring-white/10 hover:text-fog-100");

export function CityMap(props: CityMapProps) {
  const [provider, setProvider] = useState<"google" | "osm">(GOOGLE_MAPS_KEY ? "google" : "osm");
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [trafficAvailable, setTrafficAvailable] = useState(false);
  const [showTraffic, setShowTraffic] = useState(readTrafficPref);
  const [dims, setDims] = useState<"2d" | "3d">("2d");
  const [error3d, setError3d] = useState<string | null>(null);
  const view = useRef<MapView | null>(null);
  const [restore, setRestore] = useState<MapView | null>(null);

  const failGoogle = useCallback((reason: string) => {
    setGoogleError(reason);
    setTrafficAvailable(false);
    setProvider("osm");
  }, []);
  const rememberView = useCallback((v: MapView) => { view.current = v; }, []);
  const fail3d = useCallback((reason: string) => {
    setError3d(reason);
    setRestore(view.current);
    setDims("2d");
  }, []);
  const switchDims = (next: "2d" | "3d") => {
    if (next === dims) return;
    setError3d(null);
    setRestore(view.current);
    setDims(next);
  };
  const toggleTraffic = () => {
    setShowTraffic((on) => {
      writeTrafficPref(!on);
      return !on;
    });
  };

  const providerLabel = provider === "google"
    ? "Google Maps"
    : googleError
      ? "OpenStreetMap · Google Maps unavailable"
      : dims === "3d" ? "OpenStreetMap · 3D buildings (OpenFreeMap)" : "OpenStreetMap · fallback provider";
  return (
    <div className={cx("relative overflow-hidden rounded-xl ring-1 ring-white/[0.08]", props.className)}
      role="region" aria-label={props.ariaLabel ?? `Map of ${props.city.name}`}>
      {/* Google keeps its own fullscreen control top-right and Leaflet its zoom top-left: avoid both. */}
      <div className={cx("absolute top-2 z-[500] flex flex-wrap items-center gap-1.5",
        provider === "google" ? "left-2 justify-start" : "right-2 justify-end")}>
        <span className="rounded-full bg-ink-950/85 px-2.5 py-1 text-[10px] text-fog-300 ring-1 ring-inset ring-white/10"
          title={googleError ?? undefined}>
          {providerLabel}
        </span>
        {provider === "google" && trafficAvailable && (
          <button type="button" className={toggleCls(showTraffic)} aria-pressed={showTraffic} onClick={toggleTraffic}
            title="Google's visual traffic layer. GridPulse does not use it for routing or validation.">
            Show traffic
          </button>
        )}
        {provider === "osm" && (
          <div role="group" aria-label="Map view" className="inline-flex rounded-full bg-ink-950/85 p-0.5 ring-1 ring-inset ring-white/10">
            {(["2d", "3d"] as const).map((d) => (
              <button key={d} type="button" aria-pressed={dims === d} onClick={() => switchDims(d)}
                className={cx("h-6 rounded-full px-2.5 text-[11px] uppercase focus-visible:outline-2 focus-visible:outline-lilac-300",
                  dims === d ? "bg-lilac-300/20 text-lilac-100" : "text-fog-400 hover:text-fog-100")}>
                {d}
              </button>
            ))}
          </div>
        )}
      </div>
      {error3d && (
        <p role="status" className="absolute left-1/2 top-12 z-[500] -translate-x-1/2 rounded-md bg-ink-950/90 px-2.5 py-1 text-[11px] text-warn ring-1 ring-inset ring-warn/30">
          3D view unavailable ({error3d}); showing 2D.
        </p>
      )}
      <div className="h-full w-full">
        {provider === "google" && GOOGLE_MAPS_KEY ? (
          <GoogleMapCanvas {...props} apiKey={GOOGLE_MAPS_KEY} onFailure={failGoogle} showTraffic={showTraffic}
            onTrafficAvailable={setTrafficAvailable} />
        ) : dims === "3d" ? (
          <ThreeDBoundary onError={fail3d}>
            <Suspense fallback={<div className="grid h-full place-items-center bg-ink-900 text-xs text-fog-400">Loading 3D map…</div>}>
              <MapLibre3D {...props} initialView={restore} onView={rememberView} onFailure={fail3d} />
            </Suspense>
          </ThreeDBoundary>
        ) : (
          <LeafletMap key={`2d-${restore ? "restored" : "fresh"}`} {...props} initialView={restore} onView={rememberView} />
        )}
      </div>
    </div>
  );
}

export function MapLegend({ items }: { items: { color: string; label: string; dashed?: boolean; dash?: string; dot?: boolean }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-fog-400">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-2">
          {i.dot ? (
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: i.color }} />
          ) : (
            <svg width="22" height="8" aria-hidden>
              <>
                <line x1="1" y1="4" x2="21" y2="4" stroke={COLORS.casing} strokeWidth="6" strokeLinecap="round" />
                <line x1="1" y1="4" x2="21" y2="4" stroke={i.color} strokeWidth="3.5" strokeLinecap="round"
                  strokeDasharray={i.dash ? i.dash.split(" ").map((n) => Number(n) / 2).join(" ") : i.dashed ? "4 3" : undefined} />
              </>
            </svg>
          )}
          {i.label}
        </li>
      ))}
    </ul>
  );
}
