import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import type { ReactNode } from "react";
import { GlobeBackdrop, GLOBE_STILL, GLOBE_VIDEO } from "../features/landing/GlobeBackdrop";
import { ApiError, api, publicApi } from "../services/api";
import { describeAction } from "../features/incidents/shared";
import { safeNext } from "../features/auth/LoginPage";

// The real Leaflet map is exercised in its own test below; page tests use a stand-in that can "pick" a point.
vi.mock("../features/map/CityMap", async (orig) => {
  const actual = await orig<typeof import("../features/map/CityMap")>();
  return {
    ...actual,
    CityMap: (p: { onPick?: (x: [number, number]) => void; markers?: unknown[] }) => (
      <div data-testid="city-map" data-markers={p.markers?.length ?? 0}>
        {p.onPick && <button type="button" onClick={() => p.onPick!([12.899209, 74.856095])}>pick-point</button>}
      </div>
    ),
  };
});

const CITY = {
  name: "Mangaluru", state: "Karnataka", country: "India", center: [12.885, 74.855],
  coverage_bbox: [12.835, 74.815, 12.935, 74.895],
  graph: { source: "OpenStreetMap via Overpass API", license: "ODbL 1.0", osm_base_timestamp: "2026-10-24T07:42:36Z", nodes: 7348, segments: 9151, sha256: "x", weights: "" },
  attribution: "Road data © OpenStreetMap contributors (ODbL)",
  categories: ["flood", "power_outage", "road_obstruction", "fire", "other"],
  indicators: ["people_trapped", "water_rising"],
};

type Route = (url: string, init?: RequestInit) => Response | undefined;
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

function mockFetch(routes: Route) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => routes(url, init) ?? json({ detail: { code: "nf", message: `unmocked ${url}` } }, 404));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderRoutes(routes: { path: string; element: ReactNode }[], initial: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [initial] });
  return render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
}

beforeEach(() => {
  document.cookie = "gp_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---- citizen portal ---------------------------------------------------------------------------
describe("citizen report form (no login)", () => {
  async function renderReport() {
    const { default: ReportPage } = await import("../features/public/ReportPage");
    return renderRoutes([{ path: "/report", element: <ReportPage /> }, { path: "/status", element: <p>status page</p> }], "/report");
  }

  it("requires a description and a map pin before sending anything", async () => {
    const fetchFn = mockFetch((url) => (url.endsWith("/public/city") ? json(CITY) : undefined));
    await renderReport();
    await screen.findByTestId("city-map");
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByText(/at least 10 characters/)).toBeInTheDocument();
    expect(screen.getByText(/allow location access, or pick it on the map/)).toBeInTheDocument();
    expect(fetchFn.mock.calls.some(([u]) => String(u).endsWith("/public/reports"))).toBe(false);
  });

  it("submits anonymously and shows the reference", async () => {
    const fetchFn = mockFetch((url, init) => {
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/public/reports") && init?.method === "POST")
        return json({ reference: "GP-7K2M9QXA", status: "received", status_label: "Received; waiting for operator review",
          submitted_at: new Date().toISOString(), in_coverage: true, nearest_road: "Bendoor Road" }, 201);
    });
    await renderReport();
    fireEvent.click(await screen.findByRole("button", { name: /pick location on map/i }));
    fireEvent.click(screen.getByRole("button", { name: "pick-point" }));
    fireEvent.change(screen.getByLabelText("Describe what you see"), { target: { value: "Water above the kerb and rising" } });
    fireEvent.click(screen.getByLabelText("Water is rising"));
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByTestId("report-reference")).toHaveTextContent("GP-7K2M9QXA");
    const [, init] = fetchFn.mock.calls.find(([u]) => String(u).endsWith("/public/reports"))!;
    const body = JSON.parse(String(init!.body));
    expect(body).toMatchObject({ category: "flood", lat: 12.899209, lon: 74.856095, indicators: ["water_rising"] });
    expect(body).not.toHaveProperty("operator");
    // The server records the time; the client neither asks for nor sends one.
    expect(body).not.toHaveProperty("observed_at");
    expect(screen.queryByLabelText(/when did you see it/i)).toBeNull();
    expect((init!.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("shows the server's error when submission is refused", async () => {
    mockFetch((url, init) => {
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/public/reports") && init?.method === "POST")
        return json({ detail: { code: "rate_limited", message: "Too many reports from this connection." } }, 429, { "Retry-After": "300" });
    });
    await renderReport();
    fireEvent.click(await screen.findByRole("button", { name: /pick location on map/i }));
    fireEvent.click(screen.getByRole("button", { name: "pick-point" }));
    fireEvent.change(screen.getByLabelText("Describe what you see"), { target: { value: "Water above the kerb and rising" } });
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many reports");
    expect(screen.queryByTestId("report-reference")).toBeNull();
  });
});

describe("report location: detection and manual override", () => {
  type Success = (p: { coords: { latitude: number; longitude: number } }) => void;
  type Failure = (e: { code: number }) => void;
  const setGeolocation = (value: unknown) =>
    Object.defineProperty(navigator, "geolocation", { value, configurable: true, writable: true });
  function stubGeolocation(impl: (ok: Success, fail: Failure) => void) {
    const getCurrentPosition = vi.fn(impl);
    const watchPosition = vi.fn();
    setGeolocation({ getCurrentPosition, watchPosition });
    return { getCurrentPosition, watchPosition };
  }
  afterEach(() => setGeolocation(undefined));
  async function renderReport() {
    const { default: ReportPage } = await import("../features/public/ReportPage");
    return renderRoutes([{ path: "/report", element: <ReportPage /> }], "/report");
  }
  const submitBody = (fetchFn: ReturnType<typeof mockFetch>) =>
    JSON.parse(String(fetchFn.mock.calls.find(([u]) => String(u).endsWith("/public/reports"))![1]!.body));
  const reportRoutes: Route = (url, init) => {
    if (url.endsWith("/public/city")) return json(CITY);
    if (url.endsWith("/public/reports") && init?.method === "POST")
      return json({ reference: "GP-LOC00001", status: "received", status_label: "Received", submitted_at: new Date().toISOString(),
        in_coverage: true, nearest_road: null }, 201);
  };

  it("uses a granted position by default, once, without asking for a map click, and sends it only on submit", async () => {
    const geo = stubGeolocation((ok) => ok({ coords: { latitude: 12.8801, longitude: 74.8422 } }));
    const fetchFn = mockFetch(reportRoutes);
    await renderReport();
    expect(await screen.findByText("Using your detected location.")).toBeInTheDocument();
    expect(screen.getByTestId("location-summary")).toHaveTextContent("Detected location: 12.88010, 74.84220");
    expect(screen.queryByRole("button", { name: "pick-point" })).toBeNull(); // map clicks do nothing yet
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1);
    expect(geo.watchPosition).not.toHaveBeenCalled();
    expect(fetchFn.mock.calls.some(([u]) => String(u).endsWith("/public/reports"))).toBe(false);
    fireEvent.change(screen.getByLabelText("Describe what you see"), { target: { value: "Water above the kerb and rising" } });
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
    await screen.findByTestId("report-reference");
    expect(submitBody(fetchFn)).toMatchObject({ lat: 12.8801, lon: 74.8422 });
  });

  it("lets a manual pick override detection, ignores a late position, and can go back to the detected one", async () => {
    let deliver: Success = () => undefined;
    stubGeolocation((ok) => { deliver = ok; });
    mockFetch(reportRoutes);
    await renderReport();
    fireEvent.click(await screen.findByRole("button", { name: /pick location on map/i }));
    expect(screen.getByText("Click the map where the hazard is.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "pick-point" }));
    expect(screen.getByTestId("location-summary")).toHaveTextContent("Location picked on the map: 12.89921, 74.85609");
    act(() => deliver({ coords: { latitude: 12.8801, longitude: 74.8422 } })); // the browser answers late
    expect(screen.getByTestId("location-summary")).toHaveTextContent("Location picked on the map: 12.89921, 74.85609");
    fireEvent.click(screen.getByRole("button", { name: /use detected location/i }));
    expect(screen.getByTestId("location-summary")).toHaveTextContent("Detected location: 12.88010, 74.84220");
  });

  it("falls back to manual picking when permission is denied, and picking can be cancelled", async () => {
    stubGeolocation((_ok, fail) => fail({ code: 1 }));
    mockFetch(reportRoutes);
    await renderReport();
    expect(await screen.findByText(/permission was not granted/)).toBeInTheDocument();
    expect(screen.getByTestId("location-summary")).toHaveTextContent(/pick it on the map/);
    fireEvent.click(screen.getByRole("button", { name: /pick location on map/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel picking/i }));
    expect(screen.queryByRole("button", { name: "pick-point" })).toBeNull();
    expect(screen.getByRole("button", { name: /try detecting again/i })).toBeInTheDocument();
  });

  it("does not claim a detected location outside the mapped area or when geolocation is unsupported", async () => {
    stubGeolocation((ok) => ok({ coords: { latitude: 19.07, longitude: 72.87 } })); // Mumbai
    mockFetch(reportRoutes);
    await renderReport();
    expect(await screen.findByText(/outside the area GridPulse has mapped/)).toBeInTheDocument();
    expect(screen.getByTestId("location-summary")).not.toHaveTextContent("Detected location");
    cleanup();
    setGeolocation(undefined);
    await renderReport();
    expect(await screen.findByText(/cannot share its location/)).toBeInTheDocument();
  });
});

describe("speech input", () => {
  class FakeRecognition {
    static last: FakeRecognition | null = null;
    lang = ""; continuous = false; interimResults = false;
    onresult: ((e: unknown) => void) | null = null;
    onerror: ((e: { error: string }) => void) | null = null;
    onend: (() => void) | null = null;
    start = vi.fn(() => { FakeRecognition.last = this; });
    stop = vi.fn(() => this.onend?.());
    abort = vi.fn();
  }
  const result = (text: string, isFinal: boolean) => ({ resultIndex: 0, results: [{ isFinal, 0: { transcript: text } }] });
  async function renderReport() {
    mockFetch((url) => (url.endsWith("/public/city") ? json(CITY) : undefined));
    const { default: ReportPage } = await import("../features/public/ReportPage");
    return renderRoutes([{ path: "/report", element: <ReportPage /> }], "/report");
  }
  afterEach(() => { delete (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition; });

  it("appends recognised speech to the editable description and never submits", async () => {
    (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition = FakeRecognition;
    await renderReport();
    const box = await screen.findByLabelText("Describe what you see");
    fireEvent.change(box, { target: { value: "Near the bus stand." } });
    fireEvent.click(screen.getByRole("button", { name: /speak instead/i }));
    expect(screen.getByRole("button", { name: /stop listening/i })).toHaveAttribute("aria-pressed", "true");
    act(() => FakeRecognition.last!.onresult!(result("water is rising", false)));
    expect(screen.getByText(/Listening… "water is rising"/)).toBeInTheDocument();
    act(() => FakeRecognition.last!.onresult!(result("water is rising fast", true)));
    fireEvent.click(screen.getByRole("button", { name: /stop listening/i }));
    expect(box).toHaveValue("Near the bus stand. water is rising fast");
    expect(screen.getByText(/Check and edit the text before sending/)).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "Near the bus stand. Water is rising fast." } }); // still editable
    expect(box).toHaveValue("Near the bus stand. Water is rising fast.");
    expect(screen.queryByTestId("report-reference")).toBeNull();
  });

  it("explains a denied microphone and an empty transcript", async () => {
    (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition = FakeRecognition;
    await renderReport();
    fireEvent.click(await screen.findByRole("button", { name: /speak instead/i }));
    act(() => { FakeRecognition.last!.onerror!({ error: "not-allowed" }); FakeRecognition.last!.onend!(); });
    expect(screen.getByText(/Microphone access was denied/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /speak instead/i }));
    act(() => FakeRecognition.last!.onend!());
    expect(screen.getByText(/Nothing was transcribed/)).toBeInTheDocument();
  });

  it("shows a note instead of a dead button where speech recognition is unsupported", async () => {
    await renderReport();
    expect(await screen.findByTestId("speech-unsupported")).toHaveTextContent(/not available in this browser/);
    expect(screen.queryByRole("button", { name: /speak instead/i })).toBeNull();
    expect(screen.getByLabelText("Describe what you see")).not.toBeDisabled();
  });
});

describe("public status and map", () => {
  it("lists only what the public API returns and plots it", async () => {
    mockFetch((url) => {
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/public/incidents"))
        return json([{ reference: "GP-AAAA1111", category: "flood", status: "confirmed", status_label: "Confirmed by an operator",
          lat: 12.899, lon: 74.856, advisory: null, verified: true, updated_at: new Date().toISOString(), simulated_scenario: true }]);
      if (url.endsWith("/public/recent-incidents"))
        return json([{ reference: "GP-AAAA1111", category: "flood", location: "Near Bendoor Road", reported_at: new Date().toISOString(), status: "confirmed", status_label: "Confirmed by an operator" }]);
      if (url.endsWith("/public/closures")) return json([]);
    });
    const { default: StatusPage } = await import("../features/public/StatusPage");
    renderRoutes([{ path: "/status", element: <StatusPage /> }], "/status");
    expect(await screen.findByText("simulated demo")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("city-map")).toHaveAttribute("data-markers", "1"));
  });
});

// ---- operator console -------------------------------------------------------------------------
describe("operator access", () => {
  async function renderConsole(path: string) {
    const { default: AppShell } = await import("../app/AppShell");
    const { default: LoginPage } = await import("../features/auth/LoginPage");
    const { default: DashboardPage } = await import("../features/dashboard/DashboardPage");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([
      { path: "/operator/login", element: <LoginPage /> },
      { path: "/ops", element: <AppShell />, children: [{ index: true, element: <DashboardPage /> }] },
    ], { initialEntries: [path] });
    return render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
  }

  const consoleRoutes = (role: "admin" | "operator"): Route => (url) => {
    if (url.endsWith("/auth/me")) return json({ username: `ops.${role}`, display_name: role, role });
    if (url.endsWith("/scenarios")) return json([{ key: "flood_conflict", code: "A", title: "Flood with a resolvable conflict", summary: "", expected_outcome: "Conflict then reroute", lat: 12.9, lon: 74.86, category: "flood", report_text: "x" }]);
    if (url.endsWith("/simulation/state")) return json({ city: "Mangaluru", state_version: 3, epoch: 2, active_scenario: null, updated_at: new Date().toISOString(), closures: 0, alerts: [], active_routes: [], incidents_by_status: {}, pending_approvals: 0, new_reports: 0, llm_mode: "fallback" });
    if (url.includes("/incidents")) return json([]);
    if (url.includes("/audit")) return json([]);
    if (url.endsWith("/public/city")) return json(CITY);
    if (url.endsWith("/simulation/overlay")) return json({ nodes: [], zones: [], closures: [], active_routes: [], pois: [], state_version: 3, simulated_layers: [] });
  };

  it("redirects to sign-in when the backend says the session is missing", async () => {
    mockFetch((url) => (url.endsWith("/auth/me") ? json({ detail: { code: "not_authenticated", message: "Operator sign-in required" } }, 401) : undefined));
    await renderConsole("/ops");
    expect(await screen.findByRole("heading", { name: "Operator sign-in" })).toBeInTheDocument();
    expect(screen.queryByText("Report queue")).toBeNull();
  });

  it("hides admin-only scenario controls from a normal operator", async () => {
    mockFetch(consoleRoutes("operator"));
    await renderConsole("/ops");
    expect(await screen.findByText("Loading scenarios requires the admin role.")).toBeInTheDocument();
    const panel = screen.getByText("Demo scenarios").closest("section")!;
    expect(within(panel).queryByRole("button", { name: /Load/ })).toBeNull();
  });

  it("shows scenario controls to an admin", async () => {
    mockFetch(consoleRoutes("admin"));
    await renderConsole("/ops");
    const panel = (await screen.findByText("Demo scenarios")).closest("section")!;
    expect(await within(panel).findByRole("button", { name: /Load/ })).toBeInTheDocument();
  });

  it("shows no internal city-state version labels in the header or stats", async () => {
    mockFetch(consoleRoutes("admin"));
    await renderConsole("/ops");
    expect(await screen.findByText("Active routes")).toBeInTheDocument();
    expect(screen.getByText(/no demo scenario/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/state v\d|City state/);
  });

  it("only allows post-login redirects inside the console", () => {
    expect(safeNext("/ops/incidents/1")).toBe("/ops/incidents/1");
    expect(safeNext("https://evil.example")).toBe("/ops");
    expect(safeNext("//evil.example/ops")).toBe("/ops");
    expect(safeNext(null)).toBe("/ops");
  });
});

// ---- api client -------------------------------------------------------------------------------
describe("api client", () => {
  it("sends the CSRF token from its cookie on state-changing requests only", async () => {
    document.cookie = "gp_csrf=tok-123; path=/";
    const fetchFn = mockFetch(() => json({}));
    await api.execute("p1");
    await api.state();
    const [, post] = fetchFn.mock.calls[0];
    const [, get] = fetchFn.mock.calls[1];
    expect((post!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok-123");
    expect((get!.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("surfaces structured errors, 401s and network failures without inventing success", async () => {
    mockFetch(() => json({ detail: { code: "stale_approval", message: "City state changed after approval." } }, 409));
    await expect(api.execute("p1")).rejects.toMatchObject({ status: 409, code: "stale_approval" });
    mockFetch(() => json({ detail: { code: "not_authenticated", message: "Sign in" } }, 401));
    const err = await api.state().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.isUnauthenticated).toBe(true);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const net = await publicApi.city().catch((e) => e);
    expect(net.code).toBe("network_error");
  });
});

describe("plan presentation", () => {
  it("describes route actions from their computed parameters", () => {
    const text = describeAction({
      action_type: "designate_response_route", target_id: "n1",
      params: { name: "Emergency access", length_m: 6684, travel_s: 840, edge_path: ["a", "b", "c"], destination_label: "Staging point" },
    });
    expect(text).toBe("Emergency access: 6.68 km, ~14 min via 3 road segments, to Staging point");
  });
});

// ---- landing media ----------------------------------------------------------------------------
function mockReducedMotion(reduced: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce") ? reduced : false, media: query,
    addEventListener: vi.fn(), removeEventListener: vi.fn() }));
}

describe("GlobeBackdrop", () => {
  it("shows only the still image when the user prefers reduced motion", () => {
    mockReducedMotion(true);
    const { container } = render(<GlobeBackdrop />);
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector("img")?.getAttribute("src")).toBe(GLOBE_STILL);
  });

  it("layers a muted, looping video over the still and keeps it hidden until it plays", () => {
    mockReducedMotion(false);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const { container } = render(<GlobeBackdrop />);
    const video = container.querySelector("video")!;
    expect(video.getAttribute("src")).toBe(GLOBE_VIDEO);
    expect(video.muted && video.loop).toBe(true);
    expect(video.className).toContain("opacity-0");
  });
});

// ---- public layout ----------------------------------------------------------------------------
describe("public layout", () => {
  it("has no emergency warning banner", async () => {
    mockFetch(() => undefined);
    const { default: PublicShell } = await import("../features/public/PublicShell");
    renderRoutes([{ path: "/", element: <PublicShell />, children: [{ index: true, element: <p>page body</p> }] } as never], "/");
    expect(await screen.findByText("page body")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/hackathon simulation|call 112/i);
  });
});

// ---- incident page: services and resolution ---------------------------------------------------
const INCIDENT = {
  id: "11111111-1111-1111-1111-111111111111", reference: "GP-POLICE01", source: "citizen_portal", epoch: 1, scenario_key: "none",
  reporter_alias: "Anonymous citizen", report_text: "Someone broke into the shop and stole things", location_hint: null,
  location_id: null, lat: 12.89, lon: 74.85, segment_id: "w1", location_description: null, observed_at: "2026-10-24T10:00:00Z",
  indicators: [], category: "theft_robbery_burglary", urgency: "moderate", status: "EXECUTED", status_reason: "Plan v1 applied",
  verified_at: "2026-10-24T10:01:00Z", verification_note: null, dismissed_at: null, dismissal_reason: null, resolved_at: null,
  resolution_note: null, run_started_at: "2026-10-24T10:02:00Z", run_finished_at: "2026-10-24T10:03:00Z",
  created_at: "2026-10-24T10:00:00Z", updated_at: "2026-10-24T10:03:00Z", city_id: 1,
};
const WORKFLOW = { incident: INCIDENT, transitions: [{ to_status: "EXECUTED" }], plans: [], revisions: [], agent_runs: [], running: false };
const DETAIL = {
  incident: INCIDENT, assessment: null, assessment_engine: null, assessment_fallback_reason: null, evidence: null,
  proposals: [{ id: "p1", agent: "traffic", agent_run_id: "r1", revision_number: 0, summary: "Live traffic unavailable", created_at: "2026-10-24T10:02:30Z",
    payload: { status: "completed", data_source: "estimated", live_traffic: "unavailable", freshness: "2026-10-24T07:42:36Z", plan_version: 1,
      state_version: 3, route_assessments: [{ purpose: "police_response", service: "police", name: "Police response from Kadri Police Station",
        status: "clear", data_source: "OSM", travel_time_basis: "free-flow", length_m: 1200, travel_s: 180, segments: 14, closed_segments: [],
        closed_by_this_plan: [], hazard_segments: [], caution_segments: [], alternative: null,
        alternative_note: "No meaningfully different route exists under the plan's road state." }],
      traffic_management_recommendations: [], adjustments: [], conflicts: [], uncertainty: [] } }],
  is_current_epoch: true, legacy: false, verified_by: "op1", dismissed_by: null, resolved_by: null, hazard_geometry: [],
  services: [
    { service: "fire_rescue", status: "not_needed", rationale: "No fire, rescue, trapped-person or structural hazard is reported.", evidence: [], uncertainty: null },
    { service: "ambulance", status: "not_needed", rationale: "No injury is reported.", evidence: [], uncertainty: null },
    { service: "police", status: "required", rationale: "A security or criminal incident was reported.", evidence: ["reported category: theft robbery burglary"], uncertainty: null },
  ],
};

describe("incident page", () => {
  async function renderIncident() {
    const { default: IncidentLayout } = await import("../features/incidents/IncidentLayout");
    const { default: OverviewTab } = await import("../features/incidents/OverviewTab");
    return renderRoutes([
      { path: "/ops/incidents/:id", element: <IncidentLayout />, children: [{ index: true, element: <OverviewTab /> }] } as never,
      { path: "/ops", element: <p>queue</p> },
    ], `/ops/incidents/${INCIDENT.id}`);
  }

  it("shows which services are required and why, without inventing a fire response", async () => {
    mockFetch((url) => {
      if (url.endsWith("/workflow")) return json(WORKFLOW);
      if (url.endsWith(`/incidents/${INCIDENT.id}`)) return json(DETAIL);
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/simulation/overlay")) return json({ nodes: [], zones: [], closures: [], active_routes: [], pois: [], state_version: 3, simulated_layers: [] });
    });
    await renderIncident();
    const panel = (await screen.findByText("Response departments")).closest("section")!;
    const police = within(panel).getByText("Police").closest("li")!;
    expect(within(police).getByText("Required")).toBeInTheDocument();
    expect(within(police).getByText(/security or criminal incident/)).toBeInTheDocument();
    // Records made before the four-status model ("not_needed") read as "Not justified": not listed as involved.
    const toggle = within(panel).getByRole("button", { name: /Not involved: Fire & rescue, Ambulance \/ medical/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(within(panel).getByText(/No fire, rescue, trapped-person or structural hazard is reported/)).toBeInTheDocument();
    const traffic = screen.getByText("Traffic analysis").closest("section")!;
    expect(within(traffic).getByText(/Analysis of plan v1/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/city state v\d|state v\d/); // internal versions stay internal
    expect(within(traffic).getByText("Police response from Kadri Police Station")).toBeInTheDocument();
  });

  it("resolves only after confirmation, sends the status it saw, and shows the resolved state", async () => {
    let resolved = false;
    const fetchFn = mockFetch((url, init) => {
      if (url.endsWith("/workflow")) return json(resolved ? { ...WORKFLOW, incident: { ...INCIDENT, status: "RESOLVED", resolved_at: "2026-10-24T11:00:00Z" } } : WORKFLOW);
      if (url.endsWith("/resolve") && init?.method === "POST") {
        resolved = true;
        return json({ ...INCIDENT, status: "RESOLVED", resolved_at: "2026-10-24T11:00:00Z" });
      }
      if (url.endsWith(`/incidents/${INCIDENT.id}`)) return json(resolved ? { ...DETAIL, resolved_by: "op1" } : DETAIL);
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/simulation/overlay")) return json({ nodes: [], zones: [], closures: [], active_routes: [], pois: [], state_version: 3, simulated_layers: [] });
    });
    await renderIncident();
    fireEvent.click(await screen.findByRole("button", { name: /resolve emergency/i }));
    expect(fetchFn.mock.calls.some(([u]) => String(u).endsWith("/resolve"))).toBe(false); // confirmation first
    expect(screen.getByText(/report, plans and audit trail are kept/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/resolution note/i), { target: { value: "Scene cleared" } });
    const submit = screen.getAllByRole("button", { name: /resolve emergency/i }).at(-1)!;
    fireEvent.click(submit);
    expect(await screen.findByText(/no longer appears in the active/)).toBeInTheDocument();
    const call = fetchFn.mock.calls.filter(([u]) => String(u).endsWith("/resolve"));
    expect(call).toHaveLength(1);
    expect(JSON.parse(String(call[0][1]!.body))).toEqual({ note: "Scene cleared", expected_status: "EXECUTED" });
  });
});

// ---- resolution and stale client data ---------------------------------------------------------------
describe("resolved incidents leave active views", () => {
  it("drops a resolved incident from every cached queue at once, even if the refetch is slow", async () => {
    const { useIncidents, useResolve } = await import("../hooks/queries");
    let resolved = false;
    // After the POST, the refetch of the queue never arrives: only the cache update can hide the incident.
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/resolve") && init?.method === "POST") { resolved = true; return json({ ...INCIDENT, status: "RESOLVED" }); }
      if (String(url).includes("/incidents?")) {
        return resolved ? new Promise<Response>(() => undefined)
          : json([{ ...INCIDENT, current_plan_id: null, current_plan_version: 1, current_plan_status: "EXECUTED", is_current_epoch: true, legacy: false }]);
      }
      return json({}, 404);
    }));
    function Harness() {
      const q = useIncidents("current");
      const r = useResolve();
      return (
        <>
          <span data-testid="active">{q.data ? q.data.map((i) => i.reference).join(",") || "none" : "loading"}</span>
          <button type="button" onClick={() => r.mutate({ id: INCIDENT.id, expectedStatus: "EXECUTED" })}>resolve</button>
        </>
      );
    }
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Harness /></QueryClientProvider>);
    expect(await screen.findByText("GP-POLICE01")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "resolve" }));
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("none"));
  });
});

describe("relevant facilities", () => {
  it("maps only the agents' ranked candidates, never the whole facility dataset", async () => {
    const { relevantFacilities } = await import("../features/incidents/shared");
    const detail = { ...DETAIL, proposals: [...DETAIL.proposals, { id: "m1", agent: "mobility", agent_run_id: "r2", revision_number: 0, summary: "",
      created_at: "2026-10-24T10:02:20Z", payload: { facility_recommendations: [
        { facility_id: "n1760597606", name: "Kadri Police Station", kind: "police", service: "police", lat: 12.89, lon: 74.85, rank: 1,
          distance_m: 1060, travel_s: 180, source: "OpenStreetMap snapshot", capability_status: "unverified", availability_status: "unknown", rationale: "" },
        { facility_id: "nX", name: "Unlocated", kind: "police", lat: null, lon: null, distance_m: 0, travel_s: 0, source: "", capability_status: "", availability_status: "", rationale: "" },
      ] } }] };
    const pois = relevantFacilities(detail as never);
    expect(pois).toEqual([{ id: "n1760597606", kind: "police", name: "#1 Kadri Police Station", lat: 12.89, lon: 74.85, source: "OpenStreetMap snapshot" }]);
    expect(relevantFacilities(undefined)).toEqual([]);
  });
});
