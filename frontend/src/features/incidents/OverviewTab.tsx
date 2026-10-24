import { Quotes, Robot, Eye, Question, Lightbulb, Cpu } from "@phosphor-icons/react";
import { useIncidentContext } from "./IncidentLayout";
import { AGENT_LABEL, Badge, Empty, LoadingBlock, Panel, SimTag, Skeleton, StatusBadge, clock, cx, humanize, when } from "../../components/ui";
import { useCity, useOverlay } from "../../hooks/queries";
import { COLORS, CityMap, MapLegend, routeTone } from "../map/CityMap";
import { SERVICE_COLORS } from "../map/mapTheme";
import type { AgentRun, FacilityRecommendation, Plan, Proposal, ResponderRecommendation, ServiceRequirement, TrafficAnalysis } from "../../types/api";
import { km, mins, planRouteLayers, relevantFacilities } from "./shared";
import { DepartmentList, departmentsFromServices } from "./ActionPlan";

function ListBlock({ icon, title, items, tone, hint }: { icon: React.ReactNode; title: string; items: string[]; tone: string; hint: string }) {
  return (
    <div className={cx("rounded-xl border p-4", tone)}>
      <div className="flex items-center gap-2 text-sm font-medium text-fog-100">
        {icon} {title}
      </div>
      <p className="mt-0.5 text-xs text-fog-500">{hint}</p>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-fog-500">None.</p>
      ) : (
        <ul className="mt-3 grid gap-1.5 text-[13.5px] leading-relaxed text-fog-300">
          {items.map((t) => (
            <li key={t} className="flex gap-2">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-fog-500" aria-hidden />
              {t}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProposalCard({ p }: { p: Proposal }) {
  const reasoning = (p.payload.reasoning as string[] | undefined) ?? [];
  const uncertainty = (p.payload.uncertainty as string[] | undefined) ?? [];
  const consequences = (p.payload.consequences as { id: string; name: string; effect: string }[] | undefined) ?? [];
  const traffic = p.agent === "traffic" ? p.payload : null;
  return (
    <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-fog-100">{AGENT_LABEL[p.agent] ?? p.agent}</span>
        <Badge tone={p.revision_number ? "warn" : "neutral"}>{p.revision_number ? `revision ${p.revision_number}` : "initial proposal"}</Badge>
      </div>
      <p className="mt-1.5 text-[13.5px] text-fog-300">{p.summary}</p>
      {reasoning.length > 0 && (
        <ul className="mt-2 grid gap-1 text-xs leading-relaxed text-fog-500">
          {reasoning.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
      {consequences.length > 0 && (
        <p className="mt-2 text-xs text-fog-400">
          Downstream: {consequences.map((c) => `${c.id} ${c.effect}`).join(", ")}
        </p>
      )}
      {uncertainty.map((u) => (
        <p key={u} className="mt-2 text-xs text-warn">{u}</p>
      ))}
      {traffic && (
        <p className="mt-2 text-xs text-fog-500">
          Analysis of plan v{String(traffic.plan_version ?? "?")} · live traffic {String(traffic.live_traffic ?? "unavailable")}
        </p>
      )}
    </li>
  );
}


function ServicesPanel({ services, plan }: { services: ServiceRequirement[]; plan?: Plan }) {
  // Prefer the current plan's departments (with this version's route legs); fall back to the recorded decisions.
  const departments = plan?.departments?.length ? plan.departments : departmentsFromServices(services);
  return (
    <Panel title="Response departments">
      <DepartmentList departments={departments} />
      <p className="mt-3 text-xs text-fog-500">
        Only required departments get a route in the plan; it still needs evidence and your approval. A station on the map is
        not an available unit, and nothing is dispatched in the real world.
      </p>
    </Panel>
  );
}

const ROUTE_STATUS_TONE = { clear: "ok", caution: "warn", blocked: "bad" } as const;

function TrafficPanel({ t }: { t: TrafficAnalysis | null }) {
  if (!t) return null;
  return (
    <Panel title="Traffic analysis" action={<Badge tone="warn">no live traffic</Badge>}>
      <p className="text-xs text-fog-500">
        Analysis of plan v{t.plan_version ?? "?"}. Travel times are free-flow estimates from the OpenStreetMap road graph and the
        simulated road state, not observed traffic.
      </p>
      {t.status === "failed" ? (
        <p className="mt-3 text-sm text-warn">The traffic analysis failed for this plan; routes were still validated against closures and power dependencies.</p>
      ) : t.route_assessments.length === 0 ? (
        <p className="mt-3 text-sm text-fog-400">This plan has no response routes to assess.</p>
      ) : (
        <ul className="mt-3 grid gap-2.5">
          {t.route_assessments.map((r) => (
            <li key={r.purpose} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-fog-100">{r.name}</span>
                <Badge tone="accent">recommended</Badge>
                <Badge tone={ROUTE_STATUS_TONE[r.status]}>{r.status}</Badge>
              </div>
              {r.selection_reason && <p className="mt-1 text-xs text-fog-400">{r.selection_reason}</p>}
              <p className="mt-1 font-mono text-xs text-fog-400">
                {km(r.length_m)} · {mins(r.travel_s)} · {r.segments} segments
                {r.caution_segments.length ? ` · ${r.caution_segments.length} caution` : ""}
                {r.closed_segments.length ? ` · ${r.closed_segments.length} closed` : ""}
              </p>
              <p className="mt-1 text-xs text-fog-500">
                {r.alternative
                  ? `Alternative: ${km(r.alternative.length_m)}, ${mins(r.alternative.travel_s)} (${r.alternative.extra_travel_s >= 0 ? "+" : ""}${Math.round(r.alternative.extra_travel_s / 60)} min, ${r.alternative.overlap_pct}% shared). ${r.alternative.not_selected_reason ?? ""}`
                  : r.alternative_note}
              </p>
              {(r.risks ?? []).length > 0 && (
                <ul className="mt-1.5 grid gap-0.5 text-xs text-warn">
                  {r.risks!.slice(0, 4).map((x) => <li key={x}>{x}</li>)}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {(t.evidence_sources ?? []).length > 0 && (
        <details className="mt-3 text-xs text-fog-400">
          <summary className="cursor-pointer text-fog-300">Evidence sources and freshness</summary>
          <ul className="mt-1.5 grid gap-1">
            {t.evidence_sources!.map((s) => (
              <li key={s.source}><span className="text-fog-200">{s.source}</span> · {s.type} · {/^\d{4}-\d\d-\d\dT/.test(s.freshness) ? new Date(s.freshness).toLocaleString() : s.freshness}</li>
            ))}
          </ul>
        </details>
      )}
      {t.traffic_management_recommendations.length > 0 && (
        <ul className="mt-3 grid gap-1 text-xs text-fog-400">
          {t.traffic_management_recommendations.map((r, i) => (
            <li key={`${r.action}-${i}`}>
              <span className="text-fog-200">{humanize(r.action)}</span>: {r.detail} <span className="text-fog-500">({humanize(r.status)})</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function ResponderPanel({ recommendations }: { recommendations: ResponderRecommendation[] }) {
  return (
    <Panel title="Responder recommendations">
      {recommendations.length === 0 ? <Empty title="No specific responder type inferred" /> : (
        <ul className="grid gap-3">
          {recommendations.map((r) => (
            <li key={r.responder_type} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-fog-100">{humanize(r.responder_type)}</span>
                <Badge tone={r.status === "verification_required" ? "warn" : "accent"}>{humanize(r.status)}</Badge>
                <span className="text-xs text-fog-500">{Math.round(r.confidence * 100)}% confidence</span>
              </div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-fog-300">{r.rationale}</p>
              <p className="mt-1 text-xs text-fog-500">Basis: {r.evidence.join("; ")}</p>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-fog-500">These are evidence-grounded recommendations for operator review, not dispatch confirmations.</p>
    </Panel>
  );
}

function FacilityPanel({ recommendations }: { recommendations: FacilityRecommendation[] }) {
  return (
    <Panel title="Facility candidates">
      {recommendations.length === 0 ? <Empty title="No reachable facility estimate" /> : (
        <ul className="grid gap-3">
          {recommendations.map((f) => (
            <li key={f.facility_id} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-fog-100">{f.name}</span>
                <Badge>{f.kind === "police" ? "police station" : f.kind === "fire_station" ? "fire station" : "hospital"}</Badge>
                <Badge tone="info">{(f.distance_m / 1000).toFixed(2)} km straight-line</Badge>
                <Badge>{Math.max(1, Math.round(f.travel_s / 60))} min estimate</Badge>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-fog-400">{f.rationale}</p>
              <p className="mt-1 text-xs text-fog-500">Capability: {f.capability_status} · availability: {f.availability_status} · {f.source}</p>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-fog-500">GridPulse does not contact or reserve a facility. Capacity, staffing, unit availability and acceptance remain unknown unless a verified source is connected.</p>
    </Panel>
  );
}

function RunRow({ r }: { r: AgentRun }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm">
      <span className="w-24 shrink-0 text-fog-100">{AGENT_LABEL[r.agent] ?? r.agent}</span>
      <span className="text-fog-400">{humanize(r.step)}</span>
      <Badge tone={r.engine.startsWith("llm") ? "accent" : r.engine === "rules" ? "warn" : "neutral"}>
        {r.engine.startsWith("llm") ? `LLM ${r.model ?? ""}` : r.engine === "rules" ? "rule-based fallback" : "deterministic"}
      </Badge>
      {r.revision_number > 0 && <Badge>revision {r.revision_number}</Badge>}
      <span className="ml-auto font-mono text-xs text-fog-500">
        {clock(r.started_at)} · {r.duration_ms ?? 0} ms
      </span>
    </li>
  );
}

function LocationPanel() {
  const { detail, wf } = useIncidentContext();
  const city = useCity();
  const overlay = useOverlay();
  const inc = detail?.incident;
  if (!inc || inc.lat == null || inc.lon == null) return null;
  const geo = detail?.evidence?.geo;
  // The current plan's routes (the same layers as the Plans tab), so the location map shows who is going where.
  const layers = planRouteLayers(wf.plans[wf.plans.length - 1], routeTone, SERVICE_COLORS);
  return (
    <Panel title="Location" action={<span className="text-xs text-fog-500">{inc.lat.toFixed(5)}, {inc.lon.toFixed(5)}</span>}>
      {city.data ? (
        <CityMap city={city.data} className="h-72" hazard={detail?.hazard_geometry ?? []}
          focus={[...layers.routes.flatMap((r) => r.geometry), [inc.lat, inc.lon]]}
          closures={overlay.data?.closures ?? []} pois={relevantFacilities(detail)}
          signals={overlay.data?.nodes.filter((n) => n.kind === "signal") ?? []}
          routes={layers.routes}
          markers={[...layers.markers, { id: "inc", lat: inc.lat, lon: inc.lon, label: inc.reference ?? "Report", detail: "reported location" }]} />
      ) : <Skeleton className="h-72 w-full" />}
      <div className="mt-3 grid gap-2 text-sm">
        <MapLegend items={[
          { color: COLORS.incident, label: "Reported location", dot: true },
          { color: COLORS.hazard, label: "Modelled hazard area" },
          { color: COLORS.closed, label: "Existing closure", dashed: true },
          ...(relevantFacilities(detail).length ? [{ color: COLORS.poi.police, label: "Ranked facility candidates (P / F / H)", dot: true }] : []),
        ]} />
        {geo ? (
          <p className="text-fog-400">
            Snapped to <span className="text-fog-100">{geo.segment_name ?? "no road"}</span>
            {geo.snap_distance_m != null ? ` (${Math.round(geo.snap_distance_m)} m away)` : ""}; hazard model
            radius {Math.round(geo.hazard_radius_m)} m covering {geo.hazard_segment_ids.length} real road segment(s);
            simulated feeder zone {geo.zone_id ?? "none"}.
          </p>
        ) : <p className="text-fog-500">Road snapping and the hazard model are computed when the agents run.</p>}
        {inc.location_description && <p className="text-fog-500">Landmark given by the reporter: {inc.location_description}</p>}
        {inc.indicators.length > 0 && (
          <div className="flex flex-wrap gap-1.5">{inc.indicators.map((i) => <Badge key={i} tone="warn">{humanize(i)}</Badge>)}</div>
        )}
      </div>
    </Panel>
  );
}

export default function OverviewTab() {
  const { wf, detail } = useIncidentContext();
  if (!detail) return <LoadingBlock rows={4} />;
  const a = detail.assessment;
  const ev = detail.evidence;
  // Latest proposals win: a revision recomputes routes, facilities and the traffic analysis.
  const mobility = [...detail.proposals].reverse().find((p) => p.agent === "mobility" && p.payload.facility_recommendations);
  const facilities = (mobility?.payload.facility_recommendations as FacilityRecommendation[] | undefined) ?? [];
  const trafficProposal = [...detail.proposals].reverse().find((p) => p.agent === "traffic");
  const services = detail.services ?? [];
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-7">
        <Panel title="Citizen report">
          <figure className="flex gap-3">
            <Quotes size={22} weight="fill" className="shrink-0 text-lilac-300/60" />
            <div>
              <blockquote className="text-[15px] leading-relaxed text-fog-100">{detail.incident.report_text}</blockquote>
              <figcaption className="mt-2 text-xs text-fog-500">
                {detail.incident.reporter_alias}
                {` · received ${when(detail.incident.created_at)}`}
                {detail.incident.verified_at ? ` · confirmed by ${detail.verified_by}` : " · unverified until corroborated"}
              </figcaption>
            </div>
          </figure>
        </Panel>

        <LocationPanel />

        <Panel
          title="Sentinel assessment"
          action={
            detail.assessment_engine && (
              <Badge tone={detail.assessment_engine.startsWith("llm") ? "accent" : "warn"}>
                {detail.assessment_engine.startsWith("llm") ? "LLM inference" : "Rule-based fallback"}
              </Badge>
            )
          }
        >
          {!a ? (
            <Empty title={wf.running ? "Sentinel is triaging the report" : "No assessment yet"} />
          ) : (
            <div className="grid gap-4">
              {detail.assessment_fallback_reason && (
                <p className="rounded-lg bg-warn/[0.06] px-3 py-2 text-xs text-warn ring-1 ring-inset ring-warn/20">
                  {detail.assessment_fallback_reason}. Results come from transparent keyword rules, not a model.
                </p>
              )}
              <p className="text-[15px] text-fog-100">{a.summary}</p>
              <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div><dt className="text-xs text-fog-500">Category</dt><dd className="text-fog-100">{humanize(a.category)}</dd></div>
                <div><dt className="text-xs text-fog-500">Location</dt><dd className="text-fog-100">{a.location_id ?? "Unknown"}</dd></div>
                <div><dt className="text-xs text-fog-500">Urgency</dt><dd className="text-fog-100">{a.urgency}</dd></div>
                <div><dt className="text-xs text-fog-500">Confidence</dt><dd className="text-fog-100">{Math.round(a.confidence * 100)}%</dd></div>
              </dl>
              <div className="grid gap-3 sm:grid-cols-2">
                <ListBlock icon={<Quotes size={15} />} title="Reported facts" hint="Stated by the citizen" items={a.reported_facts} tone="border-white/[0.08] bg-white/[0.02]" />
                <ListBlock icon={<Lightbulb size={15} />} title="Inferences" hint="Generated by Sentinel" items={a.inferences} tone="border-lilac-300/20 bg-lilac-300/[0.04]" />
                <ListBlock icon={<Question size={15} />} title="Unknowns" hint="Missing or ambiguous" items={a.missing_information} tone="border-warn/20 bg-warn/[0.04]" />
                <ListBlock icon={<Eye size={15} />} title="Assumptions" hint="Taken without evidence" items={a.assumptions} tone="border-white/[0.08] bg-white/[0.02]" />
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Agent proposals">
          {detail.proposals.length === 0 ? (
            <Empty title={wf.running ? "Agents are preparing proposals" : "No proposals"} />
          ) : (
            <ul className="grid gap-3">{detail.proposals.map((p) => <ProposalCard key={p.id} p={p} />)}</ul>
          )}
        </Panel>
        {services.length > 0 ? <ServicesPanel services={services} plan={wf.plans[wf.plans.length - 1]} />
          : a && <ResponderPanel recommendations={a.responder_recommendations ?? []} />}
        <TrafficPanel t={(trafficProposal?.payload as unknown as TrafficAnalysis | undefined) ?? null} />
        {facilities.length > 0 && <FacilityPanel recommendations={facilities} />}
      </div>

      <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-5">
        <Panel title="Sensor evidence" action={<SimTag />}>
          {!ev ? (
            <Empty title={wf.running ? "Retrieving synthetic observations" : "No evidence retrieved"} />
          ) : (
            <div className="grid gap-4">
              {[ev.location_hazard, ...ev.infrastructure_hazards].filter(Boolean).map((h) => (
                <div key={h!.target_id} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-fog-100">{h!.target_name}</span>
                    <StatusBadge status={h!.status} />
                  </div>
                  <p className="mt-0.5 text-xs text-fog-500">{humanize(h!.hazard)}</p>
                  <p className="mt-2 text-[13px] leading-relaxed text-fog-400">{h!.explanation}</p>
                </div>
              ))}
              {ev.warnings.map((w) => (
                <p key={w} className="rounded-lg bg-warn/[0.06] px-3 py-2 text-xs text-warn ring-1 ring-inset ring-warn/20">{w}</p>
              ))}
              {ev.observations.length > 0 && <div className="scrollbar-thin overflow-x-auto">
                <table className="w-full min-w-[420px] text-left text-xs">
                  <caption className="sr-only">Synthetic sensor observations</caption>
                  <thead className="text-fog-500">
                    <tr>
                      <th className="py-2 font-normal">Sensor</th>
                      <th className="py-2 font-normal">Reading</th>
                      <th className="py-2 font-normal">Threshold</th>
                      <th className="py-2 font-normal">Observed</th>
                      <th className="py-2 font-normal">Freshness</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-fog-300">
                    {ev.observations.map((o) => (
                      <tr key={o.id} className="border-t border-white/[0.05]">
                        <td className="py-2" title={o.sensor_name}>{o.sensor_id}</td>
                        <td className={cx("py-2", o.exceeds_threshold && "text-bad")}>{o.value} {o.unit}</td>
                        <td className="py-2 text-fog-500">{o.threshold} {o.unit}</td>
                        <td className="py-2">{Math.round(o.age_minutes)} min ago</td>
                        <td className="py-2"><span className={o.freshness === "stale" ? "text-warn" : "text-fog-400"}>{o.freshness}{o.quality !== "ok" ? `, ${o.quality}` : ""}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
              {ev.operator_confirmation && (
                <p className="rounded-lg bg-lilac-300/[0.07] px-3 py-2 text-xs text-lilac-200 ring-1 ring-inset ring-lilac-300/20">
                  Operator {ev.operator_confirmation.operator} confirmed the hazard at {clock(ev.operator_confirmation.confirmed_at)}
                  {ev.operator_confirmation.note ? `: "${ev.operator_confirmation.note}"` : ""}.
                </p>
              )}
              {ev.observations.length === 0 && (
                <p className="text-xs text-fog-400">No sensor observes this location. GridPulse has no live sensor feed; synthetic
                  sensors exist only while a demo scenario is loaded.</p>
              )}
              <p className="text-xs text-fog-500">
                Readings older than {ev.fresh_window_minutes} min are stale. Every observation shown is synthetic.
              </p>
            </div>
          )}
        </Panel>

        <Panel title="Agent activity" action={<Cpu size={16} className="text-fog-500" />}>
          {wf.agent_runs.length === 0 ? (
            <Empty title="No agent runs yet" />
          ) : (
            <ul className="divide-y divide-white/[0.05]">{wf.agent_runs.map((r) => <RunRow key={r.id} r={r} />)}</ul>
          )}
          <p className="mt-3 flex items-center gap-1.5 text-xs text-fog-500">
            <Robot size={13} /> Only Sentinel's triage may use an LLM. Snapping, routing, dependencies and validation are deterministic.
          </p>
        </Panel>
      </div>
    </div>
  );
}
