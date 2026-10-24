import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { CityMap, TILE_ATTRIBUTION } from "../features/map/CityMap";
import type { City } from "../types/api";

const CITY: City = {
  name: "Mangaluru", state: "Karnataka", country: "India", center: [12.885, 74.855],
  coverage_bbox: [12.835, 74.815, 12.935, 74.895],
  graph: { source: "OSM", license: "ODbL", osm_base_timestamp: null, nodes: 1, segments: 1, sha256: "x", weights: "" },
  attribution: "", categories: [], indicators: [],
};

afterEach(cleanup);

describe("CityMap (real Leaflet in jsdom)", () => {
  it("renders the basemap attribution, incident markers and backend route geometry", () => {
    const { container } = render(
      <CityMap city={CITY} className="h-64"
        markers={[{ id: "m1", lat: 12.899, lon: 74.856, label: "GP-1" }, { id: "m2", lat: 12.88, lon: 74.84, label: "GP-2" }]}
        routes={[{ id: "r1", tone: "route", label: "Emergency access", geometry: [[12.86, 74.841], [12.87, 74.85], [12.899, 74.856]] }]} />,
    );
    expect(container.querySelector(".leaflet-container")).not.toBeNull();
    expect(container.textContent).toContain("OpenStreetMap");
    expect(TILE_ATTRIBUTION).toContain("openstreetmap.org/copyright");
    const paths = container.querySelectorAll("path.leaflet-interactive");
    // coverage outline + 1 route + 2 markers
    expect(paths.length).toBe(4);
  });
});
