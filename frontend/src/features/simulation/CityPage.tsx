import { useState } from "react";
import { useCity, useIncidents, useOverlay } from "../../hooks/queries";
import { ErrorState, Panel, Skeleton, StatusBadge, humanize } from "../../components/ui";
import { COLORS, CityMap, MapLegend, SERVICE_LEGEND, routeTone } from "../map/CityMap";

const LAYERS = [
  ["incidents", "Reports"],
  ["closures", "Closures (simulated)"],
  ["routes", "Active routes"],
  ["signals", "Traffic signals (OSM; simulated power)"],
  ["zones", "Feeder zones (simulated)"],
  ["pois", "Hospitals, fire, police (OSM)"],
  ["graph", "Routing graph (zoom in)"],
] as const;
type Layer = (typeof LAYERS)[number][0];

export default function CityPage() {
  const city = useCity();
  const overlay = useOverlay();
  const incidents = useIncidents("current");
  const [on, setOn] = useState<Record<Layer, boolean>>({
    incidents: true, closures: true, routes: true, signals: true, zones: false, pois: false, graph: false,
  });
  if (city.isError) return <ErrorState error={city.error} onRetry={() => city.refetch()} />;
  const o = overlay.data;
  return (
    <div className="grid grid-cols-1 gap-6">
      <div>
        <h1 className="font-display text-3xl font-light tracking-[-0.03em] text-fog-100">City map</h1>
        <p className="mt-1 max-w-[90ch] text-sm text-fog-400">
          Roads, signal locations and facilities come from OpenStreetMap ({city.data?.graph.segments.toLocaleString()} routable
          segments, snapshot {city.data?.graph.osm_base_timestamp?.slice(0, 10)}). Closures, feeder zones, signal power and
          sensors are part of the simulation. Travel times are estimates, not live traffic.
        </p>
      </div>
      <Panel>
        <fieldset className="mb-4 flex flex-wrap gap-x-5 gap-y-2">
          <legend className="sr-only">Map layers</legend>
          {LAYERS.map(([k, label]) => (
            <label key={k} className="inline-flex items-center gap-2 text-sm text-fog-300">
              <input type="checkbox" className="h-4 w-4 accent-lilac-300" checked={on[k]} onChange={(e) => setOn({ ...on, [k]: e.target.checked })} />
              {label}
            </label>
          ))}
        </fieldset>
        {city.data ? (
          <CityMap city={city.data} className="h-[560px]" showGraph={on.graph}
            markers={on.incidents ? (incidents.data ?? []).filter((i) => i.lat != null && i.status !== "DISMISSED").map((i) => ({
              id: i.id, lat: i.lat!, lon: i.lon!, label: i.reference ?? "report", detail: humanize(i.status) })) : []}
            closures={on.closures ? o?.closures ?? [] : []}
            routes={on.routes ? (o?.active_routes ?? []).map((r) => ({ id: `r${r.id}`, geometry: r.geometry, label: r.name, tone: routeTone(r.purpose) })) : []}
            signals={on.signals ? o?.nodes.filter((n) => n.kind === "signal") ?? [] : []}
            zones={on.zones ? o?.zones ?? [] : []} pois={on.pois ? o?.pois ?? [] : []} />
        ) : <Skeleton className="h-[560px] w-full" />}
        <div className="mt-4">
          <MapLegend items={[
            { color: COLORS.incident, label: "Report", dot: true },
            { color: COLORS.closed, label: "Closed", dashed: true },
            { color: COLORS.caution, label: "Caution", dashed: true },
            ...SERVICE_LEGEND,
            { color: COLORS.signalOk, label: "Signal powered", dot: true },
            { color: COLORS.signalDark, label: "Signal dark", dot: true },
          ]} />
        </div>
      </Panel>
      {o && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Panel title="Simulated feeder zones">
            <ul className="grid grid-cols-1 gap-1.5">
              {o.zones.map((z) => (
                <li key={z.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-fog-300"><span className="mr-2 font-mono text-xs text-fog-500">{z.id}</span>{z.name}</span>
                  <StatusBadge status={z.status === "energized" ? "APPROVED" : z.status.toUpperCase()} />
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-fog-500">Illustrative 3×3 grid over the coverage area. GridPulse has no utility data.</p>
          </Panel>
          <Panel title="Traffic signals and sensors">
            <ul className="grid grid-cols-1 gap-1.5">
              {o.nodes.map((n) => (
                <li key={n.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-fog-300">{n.name}</span>
                  <StatusBadge status={n.status === "operational" || n.status === "online" ? "APPROVED" : n.status.toUpperCase()} />
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
    </div>
  );
}
