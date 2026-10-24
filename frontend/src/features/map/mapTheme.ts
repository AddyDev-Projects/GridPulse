import type { Poi } from "../../types/api";

// One palette for every renderer (Leaflet 2D, MapLibre 3D, Google Maps) so legends stay accurate.
// Each service has ONE colour shared by its route and its facility marker. The colours avoid Google's
// traffic palette (green / orange / red / dark red), every route is drawn over a dark casing, and each
// service also has its own line pattern and marker letter so meaning never rests on colour alone.
export const SERVICE_COLORS = {
  fire_rescue: "#22d3ee", // cyan
  police: "#a78bfa", // violet
  ambulance: "#f8fafc", // white
} as const;

export const COLORS = {
  closed: "#ff7186",
  caution: "#f6c453",
  route: SERVICE_COLORS.fire_rescue, // fire & rescue access
  routeAlt: SERVICE_COLORS.ambulance, // medical transfer
  routePolice: SERVICE_COLORS.police, // police response
  conflict: "#ff2bd6", // blocked route of a superseded plan
  casing: "#07060b",
  hazard: "#ff9f6b",
  incident: "#facc15", // incidents: yellow with a white ring, unlike any service colour
  signalOk: "#4ade9b",
  signalDark: "#ff7186",
  zone: "#b4a2fb",
  fog: "#9b96ad", // neutral legend swatch (e.g. "alternative, in the service's colour")
  poi: { hospital: SERVICE_COLORS.ambulance, fire_station: SERVICE_COLORS.fire_rescue, police: SERVICE_COLORS.police } as Record<string, string>,
};

export type RouteTone = "route" | "routeAlt" | "routePolice" | "conflict";

export const ROUTE_WIDTH = 5;
export const CASING_WIDTH = 9;

/** Line pattern per tone (SVG dash array, in px). Fire & rescue is solid. */
export const ROUTE_DASH: Record<RouteTone, string | undefined> = {
  route: undefined,
  routePolice: "14 7",
  routeAlt: "2 9",
  conflict: "6 10",
};
/** Alternative candidates: thinner, translucent, short dashes, in the service's colour. */
export const ALT_DASH = "4 8";

/** Route colour by purpose, so a police route never looks like a fire & rescue route. */
export function routeTone(purpose: string | null | undefined): RouteTone {
  if (purpose === "medical_transfer") return "routeAlt";
  if (purpose === "police_response") return "routePolice";
  return "route";
}

/** Short glyphs that tell station kinds apart at a glance; the tooltip carries the full name and source. */
export const POI_GLYPH: Record<Poi["kind"], string> = { police: "P", fire_station: "F", hospital: "H" };

export const POI_LABEL: Record<Poi["kind"], string> = {
  police: "police station",
  fire_station: "fire station",
  hospital: "hospital",
};

export const poiTitle = (p: Poi) =>
  `${p.name} (${POI_LABEL[p.kind]}, OpenStreetMap). A mapped facility, not a unit availability or dispatch.`;

/** Legend entries for the three services: same colour and pattern as the routes and markers. */
export const SERVICE_LEGEND = [
  { color: COLORS.route, label: "Fire & rescue (F)", dash: ROUTE_DASH.route },
  { color: COLORS.routePolice, label: "Police (P)", dash: ROUTE_DASH.routePolice },
  { color: COLORS.routeAlt, label: "Medical (H)", dash: ROUTE_DASH.routeAlt },
];
