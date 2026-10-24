import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { ActionPlan } from "../features/incidents/ActionPlan";
import { planRouteLayers } from "../features/incidents/shared";
import { routeTone } from "../features/map/mapTheme";
import { SERVICE_COLORS } from "../features/map/mapTheme";
import type { Department, Plan, PlanAction } from "../types/api";

vi.mock("../features/map/CityMap", async (orig) => {
  const actual = await orig<typeof import("../features/map/CityMap")>();
  return {
    ...actual,
    CityMap: (p: { routes?: { id: string; label: string }[]; markers?: unknown[] }) => (
      <div data-testid="city-map">{(p.routes ?? []).map((r) => <span key={r.id} data-testid="map-route">{r.label}</span>)}</div>
    ),
  };
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const route = (seq: number, service: string, purpose: string, name: string, geometry: [number, number][] | null = [[12.87, 74.84], [12.88, 74.85]]): PlanAction => ({
  seq, action_type: "designate_response_route", label: "Designate response route", target_id: "n1", target_name: null,
  params: { purpose, service, name, origin: "n1", origin_label: `${name} origin`, destination: "n2", destination_label: `${name} destination`,
    edge_path: ["w1s0", "w2s0"], node_path: ["n1", "n9", "n2"], length_m: 2100, travel_s: 170 },
  impact: "high", proposed_by: "mobility", rationale: "Fastest path by estimated travel time.", geometry, segments_geometry: null,
} as PlanAction);

const DEPTS: Department[] = [
  { service: "police", label: "Police", status: "required", basis: "rule", rationale: "Rule: a road collision needs scene safety and traffic control.",
    evidence: ["reported category: accident"], unknowns: [], expected_action: "Respond from the fastest-reachable police station.",
    routes: [{ service: "police", leg: "to_incident", purpose: "police_response", status: "planned", action_seq: 1, name: "Police response",
      origin_label: "Kadri Police Station", destination_label: "Staging point", length_m: 2100, travel_s: 170 }] },
  { service: "ambulance", label: "Ambulance / medical", status: "required", basis: "reported", rationale: "",
    evidence: ["indicator: injuries reported"], unknowns: ["Whether anyone is injured, and how many"], expected_action: null,
    routes: [
      { service: "ambulance", leg: "to_incident", purpose: null, status: "unavailable", reason: "No ambulance station is mapped in OpenStreetMap for this area." },
      { service: "ambulance", leg: "to_hospital", purpose: "medical_transfer", status: "planned", action_seq: 2, name: "Medical transfer (incident to hospital): KMC",
        origin_label: "Staging point", destination_label: "KMC", length_m: 900, travel_s: 80 },
    ] },
  { service: "fire_rescue", label: "Fire & rescue", status: "not_justified", basis: "reported",
    rationale: "No fire, entrapment, hazardous material or other rescue need was reported at this collision.", evidence: [], unknowns: [],
    expected_action: null, routes: [] },
];

const plan = (over: Partial<Plan> = {}): Plan => ({
  id: "plan-1", incident_id: "inc-1", version: 1, parent_plan_id: null, status: "AWAITING_APPROVAL", based_on_state_version: 3,
  content_hash: "abc", summary: "Police response; medical transfer", rationale: null, created_at: "2026-10-24T10:00:00Z",
  actions: [route(1, "police", "police_response", "Police response"), route(2, "ambulance", "medical_transfer", "Medical transfer (incident to hospital): KMC")],
  latest_validation: { id: "v1", state_version: 3, trigger: "initial", passed: true, blocking_count: 0, warning_count: 1, info_count: 1,
    created_at: "2026-10-24T10:00:00Z", findings: [
      { id: 1, rule_id: "R8_TRAFFIC_DATA", severity: "warning", title: "Live traffic data unavailable", message: "Estimates only.", remediation: null, agent: "traffic", action_seq: null, evidence: {}, can_proceed: true },
      { id: 2, rule_id: "R9_DEPARTMENTS", severity: "info", title: "Ambulance starting point unknown", message: "No ambulance station.", remediation: null, agent: "mobility", action_seq: null, evidence: {}, can_proceed: true },
      { id: 3, rule_id: "R1_ROAD_SAFETY", severity: "warning", title: "Police response uses 1 caution segment", message: "Caution.", remediation: null, agent: "mobility", action_seq: 1, evidence: {}, can_proceed: true },
    ] },
  revision_from_parent: null, approvals: [], execution: null, current_state_version: 3, approvable: true, approval_blockers: [],
  departments: DEPTS, ...over,
} as Plan);

describe("ActionPlan", () => {
  it("explains each department with status, basis, evidence, expected action, unknowns and route legs", () => {
    render(<ActionPlan plan={plan()} />);
    const why = screen.getByRole("heading", { name: "Why each department is involved" }).closest("section")!;
    const police = within(why).getByText("Police").closest("li")!;
    expect(within(police).getByText("Required")).toBeInTheDocument();
    expect(within(police).getByText("Operational rule")).toBeInTheDocument();
    expect(within(police).getByText(/scene safety and traffic control/)).toBeInTheDocument();
    expect(within(police).getByText(/Kadri Police Station → Staging point/)).toBeInTheDocument();
    const amb = within(why).getByText("Ambulance / medical").closest("li")!;
    expect(within(amb).getByText("Reason not provided — review required.")).toBeInTheDocument(); // empty reason is not invented
    expect(within(amb).getByText(/To the incident:/).closest("li")).toHaveTextContent(/No valid road route could be calculated\. No ambulance station/);
    expect(within(amb).getByText(/Incident to hospital:/)).toBeInTheDocument();
    expect(within(amb).getByText(/Unknown: Whether anyone is injured/)).toBeInTheDocument();
    expect(within(why).queryByText("Fire & rescue")).toBeNull(); // not justified → only behind the toggle
    fireEvent.click(within(why).getByRole("button", { name: /Not involved: Fire & rescue/ }));
    expect(within(why).getByText(/No fire, entrapment, hazardous material/)).toBeInTheDocument();
  });

  it("lists actions in order with the responsible department and expands details on demand", () => {
    render(<ActionPlan plan={plan()} />);
    const actions = within(screen.getByRole("region", { name: "Planned actions" })).getAllByRole("button");
    expect(actions.map((b) => b.textContent)).toEqual([expect.stringMatching(/^#1.*Police/), expect.stringMatching(/^#2.*Ambulance \/ medical/)]);
    expect(actions[0]).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Distance and time")).toBeNull();
    fireEvent.click(actions[0]);
    expect(actions[0]).toHaveAttribute("aria-expanded", "true");
    const details = document.getElementById(actions[0].getAttribute("aria-controls")!)!;
    expect(within(details).getByText("Police response origin")).toBeInTheDocument();
    expect(within(details).getByText(/2\.10 km, ~3 min \(free-flow estimate\)/)).toBeInTheDocument();
    expect(within(details).getByText("Police response uses 1 caution segment")).toBeInTheDocument(); // finding tied to #1
    fireEvent.click(actions[0]);
    expect(screen.queryByText("Distance and time")).toBeNull();
  });

  it("separates hard failures, warnings and notes", () => {
    const blocked = plan({ latest_validation: { ...plan().latest_validation!, passed: false, blocking_count: 1, findings: [
      { id: 9, rule_id: "R9_DEPARTMENTS", severity: "blocking", title: "Fire & rescue is not justified for this incident", message: "x", remediation: null, agent: "mobility", action_seq: null, evidence: {}, can_proceed: false },
      ...plan().latest_validation!.findings] } });
    render(<ActionPlan plan={blocked} />);
    const v = screen.getByRole("region", { name: "Validation" });
    expect(within(v).getByText(/Hard failures \(block approval\) \(1\)/)).toBeInTheDocument();
    expect(within(v).getByText(/Warnings \(1\)/)).toBeInTheDocument();
    expect(within(v).getByText(/Notes \(1\)/)).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
  });

  it("survives missing optional fields without inventing content", () => {
    render(<ActionPlan plan={plan({ departments: undefined, latest_validation: null, summary: "",
      actions: [route(1, "police", "police_response", "Police response", null)] })} />);
    expect(screen.getByText(/made before department assessment was recorded/)).toBeInTheDocument();
    expect(screen.getByText("Not validated yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /#1/ }));
    expect(screen.getByText(/Route geometry is missing/)).toBeInTheDocument();
  });
});

describe("plan route layers", () => {
  it("draws one line per planned route in its department's colour, with start and end markers", () => {
    const layers = planRouteLayers(plan(), routeTone, SERVICE_COLORS);
    expect(layers.routes.map((r) => [r.tone, r.label])).toEqual([
      ["routePolice", "Police: Police response"], ["routeAlt", "Medical: Medical transfer (incident to hospital): KMC"]]);
    expect(layers.markers.map((m) => [m.label, m.color])).toEqual([
      ["Police start", SERVICE_COLORS.police], ["Police end", SERVICE_COLORS.police],
      ["Medical start", SERVICE_COLORS.ambulance], ["Medical end", SERVICE_COLORS.ambulance]]);
    expect([...layers.services].sort()).toEqual(["ambulance", "police"]);
    expect(layers.missing).toEqual(["Ambulance / medical: no valid road route (to the incident)"]);
  });

  it("never draws a route for a department the plan does not contain, or a route without geometry", () => {
    const layers = planRouteLayers(plan({ actions: [route(1, "police", "police_response", "Police response", [])] }), routeTone, SERVICE_COLORS);
    expect(layers.routes).toEqual([]);
    expect(layers.missing[0]).toMatch(/Police: route #1 has no drawable geometry/);
    expect(planRouteLayers(undefined, routeTone, SERVICE_COLORS).routes).toEqual([]);
  });
});

describe("changing incidents", () => {
  it("shows the newly selected incident's plan and routes, not the previous one's", async () => {
    const { default: IncidentLayout } = await import("../features/incidents/IncidentLayout");
    const { default: PlansTab } = await import("../features/incidents/PlansTab");
    const inc = (id: string, ref: string) => ({ id, reference: ref, source: "citizen_portal", epoch: 1, scenario_key: "none", reporter_alias: "a",
      report_text: "x".repeat(12), location_hint: null, location_id: null, lat: 12.87, lon: 74.84, segment_id: "w1", location_description: null,
      observed_at: null, indicators: [], category: "accident", urgency: "high", status: "AWAITING_APPROVAL", status_reason: null, verified_at: null,
      verification_note: null, dismissed_at: null, dismissal_reason: null, run_started_at: "2026-10-24T10:00:00Z", run_finished_at: "2026-10-24T10:01:00Z",
      created_at: "2026-10-24T10:00:00Z", updated_at: "2026-10-24T10:01:00Z", city_id: 1 });
    const A = { incident: inc("a", "GP-AAAAAAAA"), plans: [plan({ id: "pa", summary: "Plan for A" })] };
    const B = { incident: inc("b", "GP-BBBBBBBB"), plans: [plan({ id: "pb", summary: "Plan for B",
      actions: [route(1, "fire_rescue", "emergency_access", "Fire & rescue access")], departments: [] })] };
    const wf = (x: typeof A) => ({ ...x, transitions: [], revisions: [], agent_runs: [], running: false, max_revisions: 2 });
    const detail = (x: typeof A) => ({ incident: x.incident, assessment: null, assessment_engine: null, assessment_fallback_reason: null,
      evidence: null, proposals: [], is_current_epoch: true, legacy: false, verified_by: null, dismissed_by: null, hazard_geometry: [], services: [] });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      const body = u.includes("/incidents/a/workflow") ? wf(A) : u.includes("/incidents/b/workflow") ? wf(B)
        : u.endsWith("/incidents/a") ? detail(A) : u.endsWith("/incidents/b") ? detail(B)
        : u.endsWith("/public/city") ? { name: "Mangaluru", coverage_bbox: [12.835, 74.815, 12.935, 74.895], center: [12.885, 74.855] }
        : u.endsWith("/simulation/overlay") ? { nodes: [], zones: [], closures: [], active_routes: [], pois: [], state_version: 3, simulated_layers: [] }
        : null;
      return new Response(JSON.stringify(body), { status: body ? 200 : 404, headers: { "Content-Type": "application/json" } });
    }));
    const router = createMemoryRouter([{ path: "/ops/incidents/:id", element: <IncidentLayout />, children: [{ path: "plans", element: <PlansTab /> }] }],
      { initialEntries: ["/ops/incidents/a/plans"] });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByText("Plan for A")).toBeInTheDocument();
    expect((await screen.findAllByTestId("map-route")).map((r) => r.textContent)).toContain("Police: Police response");
    await router.navigate("/ops/incidents/b/plans");
    expect(await screen.findByText("Plan for B")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Plan for A")).toBeNull());
    await waitFor(() => expect(screen.getAllByTestId("map-route").map((r) => r.textContent)).toEqual(["Fire & rescue: Fire & rescue access"]));
  });
});
