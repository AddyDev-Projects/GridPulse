import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByText(/Tap the map to mark where it is/)).toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: "pick-point" }));
    fireEvent.change(screen.getByLabelText("Describe what you see"), { target: { value: "Water above the kerb and rising" } });
    fireEvent.click(screen.getByLabelText("Water is rising"));
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByTestId("report-reference")).toHaveTextContent("GP-7K2M9QXA");
    const [, init] = fetchFn.mock.calls.find(([u]) => String(u).endsWith("/public/reports"))!;
    const body = JSON.parse(String(init!.body));
    expect(body).toMatchObject({ category: "flood", lat: 12.899209, lon: 74.856095, indicators: ["water_rising"] });
    expect(body).not.toHaveProperty("operator");
    expect((init!.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("shows the server's error when submission is refused", async () => {
    mockFetch((url, init) => {
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/public/reports") && init?.method === "POST")
        return json({ detail: { code: "rate_limited", message: "Too many reports from this connection." } }, 429, { "Retry-After": "300" });
    });
    await renderReport();
    fireEvent.click(await screen.findByRole("button", { name: "pick-point" }));
    fireEvent.change(screen.getByLabelText("Describe what you see"), { target: { value: "Water above the kerb and rising" } });
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many reports");
    expect(screen.queryByTestId("report-reference")).toBeNull();
  });
});

describe("public status and map", () => {
  it("lists only what the public API returns and plots it", async () => {
    mockFetch((url) => {
      if (url.endsWith("/public/city")) return json(CITY);
      if (url.endsWith("/public/incidents"))
        return json([{ reference: "GP-AAAA1111", category: "flood", status: "confirmed", status_label: "Confirmed by an operator",
          lat: 12.899, lon: 74.856, advisory: null, verified: true, updated_at: new Date().toISOString(), simulated_scenario: true }]);
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
