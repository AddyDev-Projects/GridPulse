import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CityMap, TILE_ATTRIBUTION } from "../features/map/CityMap";
import type { City, Poi } from "../types/api";

const CITY: City = {
  name: "Mangaluru", state: "Karnataka", country: "India", center: [12.885, 74.855],
  coverage_bbox: [12.835, 74.815, 12.935, 74.895],
  graph: { source: "OSM", license: "ODbL", osm_base_timestamp: null, nodes: 1, segments: 1, sha256: "x", weights: "" },
  attribution: "", categories: [], indicators: [],
};
const POIS: Poi[] = [
  { id: "n1760597606", kind: "police", name: "Kadri Police Station", lat: 12.89028, lon: 74.85267, source: "OpenStreetMap" },
  { id: "w1459205289", kind: "fire_station", name: "Fire Station", lat: 12.87, lon: 74.84, source: "OpenStreetMap" },
];

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.resetModules();
  delete (window as { google?: unknown }).google;
});

describe("CityMap (real Leaflet in jsdom)", () => {
  it("renders the basemap attribution, incident markers and backend route geometry", () => {
    const { container } = render(
      <CityMap city={CITY} className="h-64"
        markers={[{ id: "m1", lat: 12.899, lon: 74.856, label: "GP-1" }, { id: "m2", lat: 12.88, lon: 74.84, label: "GP-2" }]}
        routes={[{ id: "r1", tone: "route", label: "Emergency access", geometry: [[12.86, 74.841], [12.87, 74.85], [12.899, 74.856]] }]} />,
    );
    expect(container.querySelector(".leaflet-container")).not.toBeNull();
    expect(container.textContent).toContain("OpenStreetMap");
    expect(container.textContent).toContain("OpenStreetMap · fallback provider");
    expect(TILE_ATTRIBUTION).toContain("openstreetmap.org/copyright");
    const paths = container.querySelectorAll("path.leaflet-interactive");
    // coverage outline + 1 route + 2 markers (the route's dark casing is not interactive)
    expect(paths.length).toBe(4);
    expect(container.querySelectorAll("path.gp-route-casing").length).toBe(1);
  });

  it("gives each service its own colour and line pattern, shared by its route and its station marker", async () => {
    const { COLORS, ROUTE_DASH } = await import("../features/map/mapTheme");
    const { container } = render(<CityMap city={CITY} className="h-64" pois={POIS} routes={[
      { id: "f", tone: "route", label: "Fire & rescue", geometry: [[12.86, 74.841], [12.87, 74.85]] },
      { id: "p", tone: "routePolice", label: "Police", geometry: [[12.86, 74.842], [12.88, 74.85]] },
      { id: "m", tone: "routeAlt", label: "Medical", geometry: [[12.86, 74.843], [12.89, 74.85]] },
      { id: "x", tone: "routePolice", label: "Alternative", alternative: true, geometry: [[12.86, 74.844], [12.9, 74.85]] },
    ]} />);
    const stroke = (c: string) => [...container.querySelectorAll("path.leaflet-interactive")].filter((p) => p.getAttribute("stroke") === c);
    expect(stroke(COLORS.route)[0].getAttribute("stroke-dasharray")).toBeNull();
    expect(stroke(COLORS.routePolice).map((p) => p.getAttribute("stroke-dasharray")).sort()).toEqual([ROUTE_DASH.routePolice, "4 8"].sort());
    expect(stroke(COLORS.routeAlt)[0].getAttribute("stroke-dasharray")).toBe(ROUTE_DASH.routeAlt);
    expect(container.querySelectorAll("path.gp-route-casing")).toHaveLength(3); // alternatives get no casing
    expect(new Set([COLORS.route, COLORS.routePolice, COLORS.routeAlt, COLORS.incident]).size).toBe(4);
    expect(COLORS.poi.police).toBe(COLORS.routePolice);
    expect(COLORS.poi.fire_station).toBe(COLORS.route);
    expect(COLORS.poi.hospital).toBe(COLORS.routeAlt);
  });

  it("marks police and fire stations distinctly, below routes and incidents", () => {
    const { container } = render(<CityMap city={CITY} className="h-64" pois={POIS} />);
    const police = container.querySelector(".gp-poi-police");
    expect(police?.textContent).toBe("P");
    expect(container.querySelector(".gp-poi-fire_station")?.textContent).toBe("F");
    const pane = container.querySelector(".leaflet-gp-pois-pane") as HTMLElement | null;
    expect(pane).not.toBeNull();
    expect(Number(pane!.style.zIndex)).toBeLessThan(400); // overlay pane (routes, incident markers) is 400
  });

  it("offers 2D/3D only for OpenStreetMap, starts in 2D, and falls back to 2D when 3D cannot start", async () => {
    render(<CityMap city={CITY} className="h-64" />);
    expect(screen.queryByRole("button", { name: /show traffic/i })).toBeNull();
    const two = screen.getByRole("button", { name: "2d" });
    const three = screen.getByRole("button", { name: "3d" });
    expect(two).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(three);
    // jsdom has no WebGL: the 3D view must report it and return to a working 2D map.
    await waitFor(() => expect(screen.getByText(/3D view unavailable/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "2d" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".leaflet-container")).not.toBeNull();
  });
});

describe("CityMap with Google Maps", () => {
  function fakeGoogle() {
    const setMap = vi.fn();
    class Overlay { setMap = vi.fn(); addListener = vi.fn(); close = vi.fn(); setContent = vi.fn(); open = vi.fn(); }
    class TrafficLayer { setMap = setMap; }
    class GMap {
      addListener = vi.fn(() => ({ remove: vi.fn() }));
      fitBounds = vi.fn(); setCenter = vi.fn(); setZoom = vi.fn(); getZoom = () => 13; getBounds = () => null;
    }
    class LatLngBounds { extend = vi.fn(); }
    (window as { google?: unknown }).google = {
      maps: { Map: GMap, TrafficLayer, Polyline: Overlay, Rectangle: Overlay, Marker: Overlay, InfoWindow: Overlay,
        LatLngBounds, SymbolPath: { CIRCLE: 0 } },
    };
    return setMap;
  }

  it("shows a traffic toggle (not the OSM 3D control) and toggles the visual layer without reloading", async () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key-not-real");
    vi.stubEnv("VITE_GOOGLE_MAPS_TRAFFIC_LAYER", "false");
    window.sessionStorage.clear();
    const setMap = fakeGoogle();
    const { CityMap: GoogleCityMap } = await import("../features/map/CityMap");
    render(<GoogleCityMap city={CITY} className="h-64" pois={POIS} />);
    const toggle = await screen.findByRole("button", { name: /show traffic/i });
    expect(screen.queryByRole("button", { name: "3d" })).toBeNull();
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(setMap).toHaveBeenLastCalledWith(expect.anything()));
    expect(window.sessionStorage.getItem("gridpulse:google-traffic")).toBe("1");
    fireEvent.click(toggle);
    await waitFor(() => expect(setMap).toHaveBeenLastCalledWith(null));
  });
});
