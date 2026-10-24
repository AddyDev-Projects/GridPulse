import { useState } from "react";
import { ArrowRight, CheckCircle, XCircle, GitBranch } from "@phosphor-icons/react";
import { useIncidentContext } from "./IncidentLayout";
import { ActionRow, FindingCard, km, mins, relevantFacilities, trafficFor } from "./shared";
import { useCity, useOverlay } from "../../hooks/queries";
import { AGENT_LABEL, Badge, Empty, Panel, Skeleton, StatusBadge, TextLink, cx, humanize } from "../../components/ui";
import { COLORS, CityMap, MapLegend, SERVICE_LEGEND, routeTone, type MapMarker, type MapRoute } from "../map/CityMap";
import type { Closure, LatLng, Plan, Revision } from "../../types/api";

const routesOf = (p: Plan) => p.actions.filter((a) => a.action_type === "designate_response_route");

function PlanColumn({ plan, title, revision, side }: { plan: Plan; title: string; revision?: Revision | null; side: "before" | "after" }) {
  const v = plan.latest_validation;
  const blockingSeqs = new Set(v?.findings.filter((f) => f.severity === "blocking").map((f) => f.action_seq));
  const added = new Set(revision?.diff.added.map((a) => `${a.action_type}:${a.target_id}`));
  const changed = new Set(revision?.diff.changed.map((c) => `${c.after.action_type}:${c.after.target_id}`));
  const order = ["blocking", "warning", "info"];
  const findings = [...(v?.findings ?? [])].sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));
  return (
    <Panel title={<span className="flex items-center gap-2">{title} <span className="font-mono text-xs font-normal text-fog-500">v{plan.version}</span></span>}
      action={<StatusBadge status={plan.status} />}>
      <div className="grid gap-5">
        <div className={cx("flex items-center gap-2 rounded-xl px-3.5 py-2.5 text-sm",
          v?.passed ? "bg-ok/[0.07] text-ok ring-1 ring-inset ring-ok/20" : "bg-bad/[0.07] text-bad ring-1 ring-inset ring-bad/25")}>
          {v?.passed ? <CheckCircle size={17} weight="fill" /> : <XCircle size={17} weight="fill" />}
          {v ? (v.passed ? "Passed validation" : `Blocked: ${v.blocking_count} blocking finding${v.blocking_count === 1 ? "" : "s"}`) : "Not validated"}
          {v && <span className="ml-auto text-xs text-fog-500">{v.warning_count} {v.warning_count === 1 ? "warning" : "warnings"}</span>}
        </div>
        <div>
          <h3 className="mb-2 text-xs text-fog-500">Proposed actions</h3>
          <ul className="grid gap-2">
            {plan.actions.map((a) => {
              const key = `${a.action_type}:${a.target_id}`;
              const hl = side === "before" && blockingSeqs.has(a.seq) ? "conflict"
                : side === "after" && added.has(key) ? "added" : side === "after" && changed.has(key) ? "changed" : undefined;
              return <ActionRow key={a.seq} a={a} highlight={hl} />;
            })}
          </ul>
        </div>
        <div>
          <h3 className="mb-2 text-xs text-fog-500">Validator findings</h3>
          {findings.length === 0 ? <p className="text-sm text-fog-500">No findings.</p> : <ul className="grid gap-2">{findings.map((f) => <FindingCard key={f.id} f={f} />)}</ul>}
        </div>
      </div>
    </Panel>
  );
}

function RevisionCard({ r }: { r: Revision }) {
  const purposes = Array.from(new Set([...Object.keys(r.diff.routes_before), ...Object.keys(r.diff.routes_after)]));
  return (
    <Panel title={<span className="flex items-center gap-2"><GitBranch size={16} className="text-lilac-200" /> How revision {r.revision_number} resolved it</span>}
      action={<Badge tone="accent">{r.strategy.split("+").map(humanize).join(" + ")}</Badge>}>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="grid content-start gap-3">
          <p className="text-[14px] leading-relaxed text-fog-300">{r.rationale}</p>
          <p className="text-xs text-fog-500">Triggered by: {r.triggered_by.map((t) => t.title).join("; ")}</p>
          {purposes.map((p) => {
            const b = r.diff.routes_before[p], a = r.diff.routes_after[p];
            return (
              <div key={p} className="grid gap-1.5 rounded-xl bg-white/[0.025] p-3.5 text-sm">
                <p className="text-xs text-fog-500">{humanize(p)}</p>
                <p className="text-xs"><span className="inline-block w-12 text-fog-500">Before</span>
                  <span className="text-bad">{b ? `${km(b.length_m)}, ${mins(b.travel_s)}, ${b.edge_path.length} segments` : "no route"}</span></p>
                <p className="text-xs"><span className="inline-block w-12 text-fog-500">After</span>
                  <span className="text-ok">{a ? `${km(a.length_m)}, ${mins(a.travel_s)}, ${a.edge_path.length} segments` : "route withheld"}</span>
                  {a && b && <span className="ml-2 text-fog-500">({a.length_m - b.length_m >= 0 ? "+" : ""}{Math.round(a.length_m - b.length_m)} m)</span>}</p>
              </div>
            );
          })}
          <ul className="flex flex-wrap gap-2 text-xs">
            {r.diff.added.map((a) => <Badge key={`a${a.seq}`} tone="ok">+ {humanize(a.action_type)}</Badge>)}
            {r.diff.removed.map((a) => <Badge key={`r${a.seq}`} tone="bad">− {humanize(a.action_type)}</Badge>)}
            {r.diff.changed.map((c) => <Badge key={`c${c.after.seq}`} tone="accent">changed {humanize(c.after.action_type)}</Badge>)}
          </ul>
        </div>
        <div>
          <h3 className="mb-2 text-xs text-fog-500">Alternatives the agents evaluated</h3>
          {r.alternatives.length === 0 ? <p className="text-sm text-fog-500">Single remediation path for this finding type.</p> : (
            <ul className="grid gap-2">
              {r.alternatives.map((alt, i) => (
                <li key={`${alt.strategy}${i}`} className={cx("rounded-xl border p-3.5", alt.selected ? "border-ok/35 bg-ok/[0.04]" : "border-white/[0.07]")}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-fog-100">{humanize(alt.strategy)}{alt.purpose ? ` (${humanize(alt.purpose)})` : ""}</span>
                    <Badge>{AGENT_LABEL[alt.agent] ?? alt.agent}</Badge>
                    {alt.selected ? <Badge tone="ok">selected</Badge> : <Badge tone={alt.viable ? "neutral" : "bad"}>{alt.viable ? "viable, not selected" : "not viable"}</Badge>}
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-fog-400">{alt.detail}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

function CoordinationComparison({ first, final }: { first: Plan; final: Plan }) {
  // Compare the primary responder route: fire & rescue access if planned, otherwise police response.
  const primary = (p: Plan) => routesOf(p).find((a) => a.params.purpose === "emergency_access")
    ?? routesOf(p).find((a) => a.params.purpose === "police_response");
  const a1 = primary(first);
  const af = primary(final);
  const conflict = first.latest_validation?.findings.find((f) => f.severity === "blocking");
  const rows: [string, string, string][] = [
    ["Blocking safety findings", String(first.latest_validation?.blocking_count ?? 0), String(final.latest_validation?.blocking_count ?? 0)],
    [a1?.params.purpose === "police_response" || af?.params.purpose === "police_response" ? "Police response route" : "Fire & rescue access route", a1 ? `${km(a1.params.length_m as number)}, ${mins(a1.params.travel_s as number)}` : "none",
      af ? `${km(af.params.length_m as number)}, ${mins(af.params.travel_s as number)}` : "none"],
    ["Outcome", "Blocked by the validator, never executed", humanize(final.status)],
  ];
  return (
    <Panel title="Why coordination matters">
      <p className="mb-4 max-w-[80ch] text-sm text-fog-400">
        Plan v1 is what the agents produce independently, with no cross-checking. It is never executed; it is shown as the
        uncoordinated baseline. {conflict ? `The validator found: ${conflict.title.toLowerCase()}.` : ""}
      </p>
      <div className="scrollbar-thin overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead className="text-xs text-fog-500">
            <tr><th className="py-2 font-normal" /><th className="py-2 font-normal">Uncoordinated (v{first.version})</th><th className="py-2 font-normal">GridPulse (v{final.version})</th></tr>
          </thead>
          <tbody>
            {rows.map(([k, a, b]) => (
              <tr key={k} className="border-t border-white/[0.05]">
                <td className="py-2.5 pr-4 text-fog-500">{k}</td>
                <td className="py-2.5 pr-4 font-mono text-xs text-bad">{a}</td>
                <td className="py-2.5 font-mono text-xs text-ok">{b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function PlanMap({ before, after }: { before: Plan | null; after: Plan }) {
  const city = useCity();
  const overlay = useOverlay();
  const { detail } = useIncidentContext();
  if (!city.data) return <Skeleton className="h-96 w-full" />;
  const blocked = before ?? (after.latest_validation?.passed ? null : after);
  const routes: MapRoute[] = [];
  if (blocked) routesOf(blocked).forEach((a) => a.geometry && routes.push({ id: `b${a.seq}`, geometry: a.geometry, tone: "conflict", label: `v${blocked.version}: ${a.params.name as string} (blocked)` }));
  if (after !== blocked) routesOf(after).forEach((a) => a.geometry && routes.push({
    id: `a${a.seq}`, geometry: a.geometry, tone: routeTone(a.params.purpose as string),
    label: `v${after.version}: ${a.params.name as string}` }));
  // Alternatives AnalyseTraffic evaluated for this exact plan version but did not select.
  (trafficFor(detail, after.version)?.alternative_routes ?? []).forEach((alt, i) => alt.geometry && routes.push({
    id: `alt${i}`, geometry: alt.geometry, tone: routeTone(alt.purpose), alternative: true,
    label: `${alt.name} (not selected): ${alt.not_selected_reason ?? ""}` }));
  const closures: Closure[] = after.actions.filter((a) => a.action_type === "close_roads" || a.action_type === "set_roads_caution")
    .flatMap((a) => (a.segments_geometry ?? []).map((g, i) => ({
      segment_id: `${a.seq}-${i}`, name: a.target_name ?? "Road segment", kind: a.action_type === "close_roads" ? "closed" as const : "caution" as const,
      source: "plan_execution" as const, reason: "Proposed in this plan", created_at: after.created_at, geometry: g })));
  const chainSignals = new Set((blocked ?? after).latest_validation?.findings.map((f) => f.evidence?.signal as string | undefined).filter(Boolean));
  const signals = overlay.data?.nodes.filter((n) => n.kind === "signal") ?? [];
  const markers: MapMarker[] = signals.filter((s) => chainSignals.has(s.id)).map((s) => ({
    id: `chain-${s.id}`, lat: s.lat, lon: s.lon, label: s.name, detail: "would go dark under the blocked plan", color: COLORS.conflict, radius: 13 }));
  const inc = detail?.incident;
  if (inc?.lat != null && inc.lon != null) markers.push({ id: "inc", lat: inc.lat, lon: inc.lon, label: inc.reference ?? "Report", detail: "reported location" });
  const focus: LatLng[] = routes.flatMap((r) => r.geometry);
  return (
    <div className="grid gap-3">
      <CityMap city={city.data} className="h-[460px]" routes={routes} closures={closures} hazard={detail?.hazard_geometry ?? []}
        signals={signals} pois={relevantFacilities(detail)} markers={markers}
        focus={focus.length ? focus : inc?.lat != null ? [[inc.lat, inc.lon!]] : null} />
      <MapLegend items={[
        ...SERVICE_LEGEND,
        { color: COLORS.fog, label: "Alternative (not selected)", dash: "4 8" },
        { color: COLORS.conflict, label: "Blocked route (plan before revision)", dash: "6 10" },
        { color: COLORS.closed, label: "Closures in this plan", dashed: true },
        { color: COLORS.hazard, label: "Modelled hazard area" },
        { color: COLORS.signalOk, label: "OSM traffic signal (simulated power)", dot: true },
      ]} />
      <p className="text-xs text-fog-500">Lines are drawn from the exact road-graph segments each plan contains, the same ones the validator checked.</p>
    </div>
  );
}

export default function PlansTab() {
  const { wf } = useIncidentContext();
  const [revIdx, setRevIdx] = useState<number | null>(null);
  if (wf.plans.length === 0) return <Empty title={wf.running ? "The coordinator is assembling the first plan" : "No plans yet"} />;
  const revisions = wf.revisions;
  const idx = revIdx ?? revisions.length - 1;
  const rev = revisions[idx];
  const before = rev ? wf.plans.find((p) => p.id === rev.from_plan_id)! : null;
  const after = rev ? wf.plans.find((p) => p.id === rev.to_plan_id)! : wf.plans[wf.plans.length - 1];

  return (
    <div className="grid grid-cols-1 gap-6">
      {revisions.length > 1 && (
        <div role="tablist" aria-label="Revision" className="flex gap-2">
          {revisions.map((r, i) => (
            <button key={r.id} role="tab" aria-selected={i === idx} onClick={() => setRevIdx(i)}
              className={cx("rounded-full px-3 py-1.5 text-xs", i === idx ? "bg-white/10 text-fog-100" : "text-fog-400 hover:text-fog-100")}>
              Revision {r.revision_number}
            </button>
          ))}
        </div>
      )}
      <Panel title="Plan on the map of Mangaluru" action={<TextLink to="/ops/city">Open city map</TextLink>}>
        <PlanMap before={before} after={after} />
      </Panel>
      {rev && before ? (
        <>
          <RevisionCard r={rev} />
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            <PlanColumn plan={before} title="Before" side="before" />
            <div className="hidden items-center xl:flex" aria-hidden><ArrowRight size={22} className="text-fog-500" /></div>
            <PlanColumn plan={after} title="After" side="after" revision={rev} />
          </div>
        </>
      ) : (
        <PlanColumn plan={after} title="Plan" side="before" />
      )}
      {wf.plans.length > 1 && <CoordinationComparison first={wf.plans[0]} final={wf.plans[wf.plans.length - 1]} />}
    </div>
  );
}
