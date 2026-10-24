import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CircleMarker,
  MapContainer,
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

export const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
export const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

export const COLORS = {
  closed: "#ff7186",
  caution: "#f6c453",
  route: "#4ade9b",
  routeAlt: "#7cc7ff",
  conflict: "#ff7186",
  hazard: "#ff9f6b",
  incident: "#f3b8f5",
  signalOk: "#4ade9b",
  signalDark: "#ff7186",
  zone: "#b4a2fb",
  poi: { hospital: "#7cc7ff", fire_station: "#ff9f6b", police: "#cfc2ff" } as Record<string, string>,
};

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
  tone: "route" | "routeAlt" | "conflict";
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

function FitTo({ points, fallback }: { points: LatLng[] | null | undefined; fallback: LatLngBoundsExpression }) {
  const map = useMap();
  const key = points ? `${points.length}:${points[0]?.join(",")}:${points[points.length - 1]?.join(",")}` : "none";
  useEffect(() => {
    if (points && points.length > 1) map.fitBounds(points as LatLngBoundsExpression, { padding: [36, 36], maxZoom: 17 });
    else if (points && points.length === 1) map.setView(points[0] as LatLngExpression, 16);
    else map.fitBounds(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
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

export function CityMap({
  city, className, markers = [], closures = [], routes = [], hazard = [], signals = [], zones = [], pois = [],
  showGraph = false, focus, picked, onPick, ariaLabel,
}: CityMapProps) {
  const [s, w, n, e] = city.coverage_bbox;
  const coverage: LatLngBoundsExpression = useMemo(() => [[s, w], [n, e]], [s, w, n, e]);
  return (
    <div className={cx("relative overflow-hidden rounded-xl ring-1 ring-white/[0.08]", className)}
      role="region" aria-label={ariaLabel ?? `Map of ${city.name}`}>
      <MapContainer bounds={coverage} maxBounds={[[s - 0.05, w - 0.05], [n + 0.05, e + 0.05]]} minZoom={12} maxZoom={19}
        className="h-full w-full bg-ink-900" scrollWheelZoom style={{ cursor: onPick ? "crosshair" : undefined }}>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains="abcd" maxZoom={19} />
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
        {routes.map((r) => (
          <Polyline key={r.id} positions={r.geometry}
            pathOptions={{ color: COLORS[r.tone], weight: 5, opacity: 0.95, dashArray: r.tone === "conflict" ? "10 8" : undefined }}>
            <Tooltip sticky>{r.label}</Tooltip>
          </Polyline>
        ))}
        {pois.map((p) => (
          <CircleMarker key={p.id} center={[p.lat, p.lon]} radius={5}
            pathOptions={{ color: COLORS.poi[p.kind] ?? "#c9c5d6", fillOpacity: 0.85, weight: 1 }}>
            <Tooltip>{`${p.name} (${p.kind.replace("_", " ")}, OpenStreetMap)`}</Tooltip>
          </CircleMarker>
        ))}
        {signals.map((sg) => (
          <CircleMarker key={sg.id} center={[sg.lat, sg.lon]} radius={6}
            pathOptions={{ color: sg.status === "operational" ? COLORS.signalOk : COLORS.signalDark, fillOpacity: 0.9, weight: 2 }}>
            <Tooltip>{`${sg.name}: ${sg.status}. Location from OSM; power model simulated.`}</Tooltip>
          </CircleMarker>
        ))}
        {markers.map((m) => (
          <CircleMarker key={m.id} center={[m.lat, m.lon]} radius={m.radius ?? 9}
            eventHandlers={m.onClick ? { click: m.onClick } : undefined}
            pathOptions={{ color: m.color ?? COLORS.incident, fillOpacity: 0.85, weight: 2 }}>
            <Tooltip>{m.detail ? `${m.label}: ${m.detail}` : m.label}</Tooltip>
          </CircleMarker>
        ))}
        {picked && (
          <CircleMarker center={picked} radius={11} pathOptions={{ color: "#ffffff", fillColor: COLORS.incident, fillOpacity: 0.9, weight: 3 }}>
            <Tooltip permanent direction="top" offset={[0, -10]}>Reported location</Tooltip>
          </CircleMarker>
        )}
        {onPick && <PickHandler onPick={onPick} />}
        <FitTo points={focus} fallback={coverage} />
      </MapContainer>
    </div>
  );
}

export function MapLegend({ items }: { items: { color: string; label: string; dashed?: boolean; dot?: boolean }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-fog-400">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-2">
          {i.dot ? (
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: i.color }} />
          ) : (
            <svg width="22" height="8" aria-hidden>
              <line x1="1" y1="4" x2="21" y2="4" stroke={i.color} strokeWidth="4" strokeDasharray={i.dashed ? "4 3" : undefined} strokeLinecap="round" />
            </svg>
          )}
          {i.label}
        </li>
      ))}
    </ul>
  );
}
