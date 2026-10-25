import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { City } from "../types/api";

/**
 * Google Maps lifecycle against a fake Maps JavaScript API. These tests prove the app's own handling
 * (loading, failure detection, fallback, overlay bookkeeping); they are NOT proof that live Google
 * tiles render — that needs a real key in a real browser.
 */

const CITY: City = {
  name: "Mangaluru", state: "Karnataka", country: "India", center: [12.885, 74.855],
  coverage_bbox: [12.835, 74.815, 12.935, 74.895],
  graph: { source: "OSM", license: "ODbL", osm_base_timestamp: null, nodes: 1, segments: 1, sha256: "x", weights: "" },
  attribution: "", categories: [], indicators: [],
};
const POLICE = { id: "p1", tone: "routePolice" as const, label: "Police", geometry: [[12.86, 74.84], [12.87, 74.85], [12.88, 74.86]] as [number, number][] };
const FIRE = { id: "f1", tone: "route" as const, label: "Fire & rescue", geometry: [[12.861, 74.841], [12.881, 74.861]] as [number, number][] };

function fakeGoogle() {
  const created = { polylines: [] as FakeOverlay[], markers: [] as FakeOverlay[], maps: [] as FakeMap[] };
  class FakeOverlay {
    map: unknown; opts: Record<string, unknown>;
    setMap = vi.fn((m: unknown) => { this.map = m; });
    addListener = vi.fn(() => ({ remove: vi.fn() }));
    close = vi.fn(); setContent = vi.fn(); open = vi.fn();
    constructor(opts: Record<string, unknown> = {}) { this.opts = opts; this.map = opts.map; }
  }
  class Polyline extends FakeOverlay { constructor(o: Record<string, unknown>) { super(o); created.polylines.push(this); } }
  class Marker extends FakeOverlay { constructor(o: Record<string, unknown>) { super(o); created.markers.push(this); } }
  class FakeMap {
    listeners: Record<string, () => void> = {};
    fitBounds = vi.fn(); setCenter = vi.fn(); setZoom = vi.fn(); getZoom = () => 13; getBounds = () => null;
    addListener = vi.fn((name: string, fn: () => void) => { this.listeners[name] = fn; return { remove: vi.fn() }; });
    el: HTMLElement;
    constructor(el: HTMLElement) { this.el = el; created.maps.push(this); }
  }
  class LatLngBounds { extend = vi.fn(); }
  const api = { maps: { Map: FakeMap, TrafficLayer: FakeOverlay, Polyline, Rectangle: FakeOverlay, Marker, InfoWindow: FakeOverlay,
    LatLngBounds, SymbolPath: { CIRCLE: 0 } } };
  return { api, created };
}

/** Intercept the Google <script> the loader appends, and decide what "loading" it does. */
function interceptScript(onScript: (s: HTMLScriptElement) => void) {
  const original = document.head.appendChild.bind(document.head);
  return vi.spyOn(document.head, "appendChild").mockImplementation((node: Node) => {
    if (node instanceof HTMLScriptElement && node.dataset.gridpulseGoogleMaps) {
      original(node);
      onScript(node);
      return node;
    }
    return original(node);
  });
}

async function loadCityMap() {
  vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key-not-real");
  vi.resetModules();
  return (await import("../features/map/CityMap")).CityMap;
}

beforeEach(() => {
  delete (window as { google?: unknown }).google;
  delete (window as { gm_authFailure?: unknown }).gm_authFailure;
  document.querySelectorAll("script[data-gridpulse-google-maps]").forEach((s) => s.remove());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("Google Maps loading and failure handling", () => {
  it("falls back to OpenStreetMap with Google's reason when the key is rejected after the script loads", async () => {
    const { api } = fakeGoogle();
    interceptScript((s) => {
      (window as { google?: unknown }).google = api;
      s.onload?.(new Event("load"));
    });
    const CityMap = await loadCityMap();
    render(<CityMap city={CITY} className="h-64" routes={[POLICE]} />);
    expect(await screen.findByText("Google Maps")).toBeInTheDocument(); // loaded and mounted: not yet known to be broken
    // What Google does for a referrer-restricted key: log the code, then call gm_authFailure.
    act(() => {
      console.error("Google Maps JavaScript API error: RefererNotAllowedMapError\nhttps://developers.google.com/...");
      window.gm_authFailure?.();
    });
    expect(await screen.findByText(/HTTP-referrer restrictions/)).toBeInTheDocument();
    expect(screen.getByText("(RefererNotAllowedMapError)")).toBeInTheDocument();
    expect(screen.getByText("OpenStreetMap · Google Maps unavailable")).toBeInTheDocument();
    expect(document.querySelector(".leaflet-container")).not.toBeNull(); // the routes are still on a working map
    expect(screen.queryByRole("button", { name: /try google maps again/i })).toBeNull(); // config problem: retry won't help
  });

  it("offers a retry after a network failure, and the retry renders Google Maps", async () => {
    const { api, created } = fakeGoogle();
    let attempt = 0;
    interceptScript((s) => {
      attempt += 1;
      if (attempt === 1) s.onerror?.(new Event("error"));
      else { (window as { google?: unknown }).google = api; s.onload?.(new Event("load")); }
    });
    const CityMap = await loadCityMap();
    render(<CityMap city={CITY} className="h-64" routes={[POLICE]} />);
    expect(await screen.findByText(/could not be downloaded/)).toBeInTheDocument();
    expect(document.querySelectorAll("script[data-gridpulse-google-maps]")).toHaveLength(0); // failed tag removed
    fireEvent.click(screen.getByRole("button", { name: /try google maps again/i }));
    await waitFor(() => expect(created.maps).toHaveLength(1));
    expect(screen.getByLabelText("Google Maps")).toBeInTheDocument();
    expect(attempt).toBe(2);
  });

  it("falls back if Google never draws a tile", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = fakeGoogle();
    (window as { google?: unknown }).google = api; // already loaded; tiles simply never arrive
    const CityMap = await loadCityMap();
    render(<CityMap city={CITY} className="h-64" />);
    await screen.findByText("Google Maps");
    await act(async () => { vi.advanceTimersByTime(15_500); });
    expect(await screen.findByText(/did not draw any map tiles/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try google maps again/i })).toBeInTheDocument();
  });

  it("treats a stalled map as a configuration problem when Google already reported a key error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = fakeGoogle();
    (window as { google?: unknown }).google = api;
    const CityMap = await loadCityMap();
    render(<CityMap city={CITY} className="h-64" />);
    await screen.findByText("Google Maps");
    console.warn("Google Maps JavaScript API warning: InvalidKey https://developers.google.com/...");
    await act(async () => { vi.advanceTimersByTime(15_500); });
    expect(await screen.findByText(/the API key is not valid/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /try google maps again/i })).toBeNull();
  });
});

describe("Google Maps overlays", () => {
  async function renderReady(routes = [POLICE, FIRE]) {
    const { api, created } = fakeGoogle();
    (window as { google?: unknown }).google = api;
    const CityMap = await loadCityMap();
    const view = render(<CityMap city={CITY} className="h-64" routes={routes} focus={POLICE.geometry} />);
    await waitFor(() => expect(created.maps).toHaveLength(1));
    act(() => created.maps[0].listeners.tilesloaded?.()); // tiles arrived: no watchdog fallback
    return { ...view, created, CityMap };
  }
  const routeLines = (created: ReturnType<typeof fakeGoogle>["created"]) =>
    created.polylines.filter((p) => p.map && (p.opts.zIndex === 20 || p.opts.zIndex === 21));

  it("draws each route as a casing and a core, in {lat, lng} order, in its service colour", async () => {
    const { created } = await renderReady();
    const lines = routeLines(created);
    expect(lines).toHaveLength(4); // 2 routes × (casing + core)
    const policeCore = lines.find((p) => p.opts.zIndex === 21 && (p.opts.path as { lat: number }[]).length === 3)!;
    expect(policeCore.opts.path).toEqual([{ lat: 12.86, lng: 74.84 }, { lat: 12.87, lng: 74.85 }, { lat: 12.88, lng: 74.86 }]);
    expect(policeCore.opts.strokeColor).toBe("#a78bfa");
    expect((policeCore.opts.icons as unknown[]).length).toBe(1); // police is dashed (symbols), fire is solid
    const fireCore = lines.find((p) => p.opts.zIndex === 21 && p !== policeCore)!;
    expect(fireCore.opts.strokeColor).toBe("#22d3ee");
    expect(fireCore.opts.icons).toBeUndefined();
  });

  it("does not rebuild or re-fit on a routine re-render, replaces obsolete routes, and cleans up on unmount", async () => {
    const { created, rerender, unmount, CityMap } = await renderReady();
    const map = created.maps[0];
    const before = created.polylines.length;
    const fits = map.fitBounds.mock.calls.length;
    // Same content, new arrays (what polling does).
    rerender(<CityMap city={CITY} className="h-64" routes={[{ ...POLICE }, { ...FIRE }]} focus={[...POLICE.geometry]} />);
    expect(created.polylines.length).toBe(before);
    expect(map.fitBounds.mock.calls.length).toBe(fits);
    // The plan changes: the fire route goes away.
    rerender(<CityMap city={CITY} className="h-64" routes={[POLICE]} focus={POLICE.geometry} />);
    expect(routeLines(created)).toHaveLength(2);
    unmount();
    expect(created.polylines.filter((p) => p.map)).toHaveLength(0);
    expect(created.markers.filter((m) => m.map)).toHaveLength(0);
  });

  it("never draws a route without geometry", async () => {
    const { created } = await renderReady([POLICE, { ...FIRE, geometry: [] }]);
    expect(routeLines(created)).toHaveLength(2);
  });
});
