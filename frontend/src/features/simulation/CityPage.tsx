import { useState } from "react";
import { useGraph, useSimState } from "../../hooks/queries";
import { ErrorState, Panel, SimTag, Skeleton, StatusBadge, cx, humanize } from "../../components/ui";
import { CityGraph, GraphLegend } from "./CityGraph";

const KIND_ORDER = ["substation", "signal", "pump", "sensor"] as const;

export default function CityPage() {
  const graph = useGraph();
  const state = useSimState();
  const [sensors, setSensors] = useState(true);

  if (graph.isError) return <ErrorState error={graph.error} onRetry={() => graph.refetch()} />;
  const g = graph.data;
  return (
    <div className="grid grid-cols-1 gap-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-3xl font-light tracking-[-0.03em] text-fog-100">City graph</h1>
          <p className="mt-1 text-sm text-fog-400">
            The same road and dependency graph the agents and validator use. Layout positions are schematic, not geography.
          </p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm text-fog-300">
          <input type="checkbox" checked={sensors} onChange={(e) => setSensors(e.target.checked)} className="h-4 w-4 accent-lilac-300" />
          Show synthetic sensors
        </label>
      </div>
      <Panel title={state.data ? `Scenario ${state.data.active_scenario.code}: ${state.data.active_scenario.title}` : "City"} action={<SimTag />}>
        {g ? (
          <CityGraph graph={g} showSensors={sensors}
            routes={g.active_routes.map((r) => ({ node_path: r.node_path, label: r.name, tone: "active" as const }))} />
        ) : (
          <Skeleton className="h-96 w-full" />
        )}
        <div className="mt-4"><GraphLegend /></div>
      </Panel>

      {g && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Panel title="Infrastructure">
            <div className="grid gap-5">
              {KIND_ORDER.map((k) => (
                <div key={k}>
                  <h3 className="mb-2 text-xs text-fog-500">{humanize(k)}s</h3>
                  <ul className="grid gap-1.5">
                    {g.nodes.filter((n) => n.kind === k).map((n) => (
                      <li key={n.id} className="flex items-center justify-between gap-3 text-sm">
                        <span className="text-fog-300"><span className="mr-2 font-mono text-xs text-fog-500">{n.id}</span>{n.name}</span>
                        <StatusBadge status={n.status === "operational" || n.status === "energized" || n.status === "running" || n.status === "online" ? "APPROVED" : n.status.toUpperCase()} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Roads and power feeds">
            <ul className="grid gap-1.5">
              {g.edges.filter((e) => e.kind !== "controls").map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className={cx("text-fog-300", e.kind === "power_feed" && "text-fog-400")}>
                    <span className="mr-2 font-mono text-xs text-fog-500">{e.id}</span>{e.name}
                    {e.length_m ? <span className="ml-2 text-xs text-fog-500">{e.length_m} m</span> : null}
                  </span>
                  <span className={cx("text-xs", e.status === "open" || e.status === "active" ? "text-fog-400" : e.status === "standby" ? "text-fog-500" : "text-bad")}>
                    {e.status}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
    </div>
  );
}
