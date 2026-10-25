import { useEffect, useMemo, useRef, useState } from "react";
import { publicApi } from "../../services/api";
import type { CityMapProps } from "./CityMap";
import { ALT_DASH, CASING_WIDTH, COLORS, POI_GLYPH, ROUTE_DASH, ROUTE_WIDTH, poiTitle } from "./mapTheme";

type GoogleApi = any;

declare global {
  interface Window {
    google?: GoogleApi;
    gm_authFailure?: () => void;
  }
}

/** Why Google Maps is not usable, in words an operator can act on. */
export interface GoogleMapsFailure {
  reason: string;
  /** Google's own error code (e.g. RefererNotAllowedMapError) when it reported one. */
  code?: string;
  /** True for network / timeout problems a retry can fix; false for key or Google Cloud configuration. */
  retryable: boolean;
}

// Google's documented key/configuration errors (developers.google.com/maps/documentation/javascript/error-messages).
const GOOGLE_ERRORS: Record<string, string> = {
  InvalidKeyMapError: "the API key is not valid",
  InvalidKey: "the API key is not valid",
  MissingKeyMapError: "no API key was sent",
  RefererNotAllowedMapError: "this site's address is not allowed by the key's HTTP-referrer restrictions",
  ApiNotActivatedMapError: "the Maps JavaScript API is not enabled for the key's Google Cloud project",
  ApiTargetBlockedMapError: "the key's API restrictions do not include the Maps JavaScript API",
  BillingNotEnabledMapError: "billing is not enabled for the key's Google Cloud project",
  ExpiredKeyMapError: "the API key has expired",
  DeletedApiProjectMapError: "the key's Google Cloud project was deleted",
  ProjectDeniedMapError: "the key's Google Cloud project was denied access",
  OverQuotaMapError: "the project is over its Maps quota",
};
const SCRIPT_TIMEOUT_MS = 15_000;
const TILES_TIMEOUT_MS = 15_000;
const CONFIG_HINT = "Fix the key in Google Cloud (or VITE_GOOGLE_MAPS_API_KEY in the repository-root .env), restart npm run dev and reload.";

let googleMapsPromise: Promise<GoogleApi> | null = null;
let authFailure: GoogleMapsFailure | null = null;
let lastGoogleCode: string | undefined;
let diagnosticsInstalled = false;
const failureListeners = new Set<(f: GoogleMapsFailure) => void>();

function describeAuthFailure(): GoogleMapsFailure {
  const what = (lastGoogleCode && GOOGLE_ERRORS[lastGoogleCode]) ?? "Google rejected the API key (invalid key, referrer restriction, billing, or API not enabled)";
  return { reason: `Google Maps cannot be used: ${what}. ${CONFIG_HINT}`, code: lastGoogleCode, retryable: false };
}

/**
 * Google reports a rejected key AFTER its script has loaded, by calling window.gm_authFailure, and logs the
 * exact error code to the console. Both are watched for the life of the page; the original handler and
 * console output are left untouched.
 */
function installDiagnostics() {
  if (diagnosticsInstalled) return;
  diagnosticsInstalled = true;
  const previous = window.gm_authFailure;
  window.gm_authFailure = () => {
    previous?.();
    authFailure = describeAuthFailure();
    failureListeners.forEach((notify) => notify(authFailure!));
  };
  for (const level of ["error", "warn"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      const m = typeof args[0] === "string" ? /Google Maps JavaScript API (error|warning): (\w+)/.exec(args[0]) : null;
      if (m && (m[1] === "error" || !lastGoogleCode)) lastGoogleCode = m[2];
      original(...args);
    };
  }
}

/** Forget a failed network load so the next mount can try again (a rejected key needs a reload instead). */
export function resetGoogleMapsLoader() {
  if (window.google?.maps) return;
  googleMapsPromise = null;
  document.querySelectorAll("script[data-gridpulse-google-maps]").forEach((s) => s.remove());
}

function loadGoogleMaps(apiKey: string): Promise<GoogleApi> {
  installDiagnostics(); // also when Google is already on the page: its errors arrive later either way
  if (authFailure) return Promise.reject(authFailure);
  if (window.google?.maps) return Promise.resolve(window.google);
  if (googleMapsPromise) return googleMapsPromise;
  googleMapsPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.async = true;
    script.defer = true;
    script.dataset.gridpulseGoogleMaps = "true";
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&v=weekly`;
    const fail = (f: GoogleMapsFailure) => {
      window.clearTimeout(timer);
      googleMapsPromise = null;
      script.remove();
      reject(f);
    };
    const timer = window.setTimeout(() => fail({
      reason: "The Google Maps script did not load within 15 seconds (network, firewall or ad-blocker).", retryable: true,
    }), SCRIPT_TIMEOUT_MS);
    script.onload = () => {
      window.clearTimeout(timer);
      if (window.google?.maps) resolve(window.google);
      else fail({ reason: "The Google Maps script loaded without its JavaScript API.", retryable: true });
    };
    script.onerror = () => fail({ reason: "The Google Maps script could not be downloaded (network, firewall or ad-blocker).", retryable: true });
    document.head.appendChild(script);
  });
  return googleMapsPromise;
}

function path(points: [number, number][]) {
  return points.map(([lat, lng]) => ({ lat, lng }));
}

export function GoogleMapCanvas(props: CityMapProps & {
  apiKey: string;
  onFailure: (failure: GoogleMapsFailure) => void;
  showTraffic?: boolean;
  onTrafficAvailable?: (available: boolean) => void;
}) {
  // Callbacks and the graph toggle are read through `latest` so the map never needs re-creating for them.
  const {
    apiKey, city, markers = [], closures = [], routes = [], hazard = [], signals = [], zones = [], pois = [],
    focus, picked, showTraffic = false, scrollZoom = false,
  } = props;
  const rootRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<GoogleApi>(null);
  const overlayRef = useRef<GoogleApi[]>([]);
  const graphOverlayRef = useRef<GoogleApi[]>([]);
  const infoWindowRef = useRef<GoogleApi>(null);
  const trafficRef = useRef<GoogleApi>(null);
  const latest = useRef(props);
  const [ready, setReady] = useState(false);
  const [graphNote, setGraphNote] = useState<string | null>(null);
  const graphKeyRef = useRef<string | null>(null);
  const graphLoadingRef = useRef(false);

  useEffect(() => { latest.current = props; });

  useEffect(() => {
    let cancelled = false;
    let listeners: GoogleApi[] = [];
    let tilesTimer: number | undefined;
    const failed = (f: GoogleMapsFailure) => { if (!cancelled) latest.current.onFailure(f); };
    failureListeners.add(failed);
    loadGoogleMaps(apiKey)
      .then((g) => {
        if (cancelled || !rootRef.current) return;
        const [south, west, north, east] = city.coverage_bbox;
        const map = new g.maps.Map(rootRef.current, {
          center: { lat: city.center[0], lng: city.center[1] },
          zoom: 13,
          minZoom: 12,
          maxZoom: 19,
          streetViewControl: false,
          fullscreenControl: true,
          mapTypeControl: false,
          clickableIcons: false,
          // "cooperative": Ctrl/⌘+wheel zooms and plain wheel scrolls the page, unless the page opts in.
          gestureHandling: scrollZoom ? "greedy" : "cooperative",
          backgroundColor: "#0c0a12",
          styles: [
            { elementType: "geometry", stylers: [{ color: "#161320" }] },
            { elementType: "labels.text.fill", stylers: [{ color: "#c9c5d6" }] },
            { elementType: "labels.text.stroke", stylers: [{ color: "#0c0a12" }] },
            { featureType: "road", elementType: "geometry", stylers: [{ color: "#2e2840" }] },
            { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#4b4167" }] },
            { featureType: "water", elementType: "geometry", stylers: [{ color: "#101b2d" }] },
            // Google's own points of interest (shops, restaurants, its hospital pins) and transit icons compete
            // with the facilities GridPulse ranked for this incident, so the basemap shows streets and labels only.
            { featureType: "poi", stylers: [{ visibility: "off" }] },
            { featureType: "transit", stylers: [{ visibility: "off" }] },
          ],
        });
        mapRef.current = map;
        // Google's TrafficLayer is a picture for the operator only. It is never read back as data:
        // GridPulse routing and validation use the OSM graph and simulated closures.
        if (g.maps.TrafficLayer) trafficRef.current = new g.maps.TrafficLayer();
        latest.current.onTrafficAvailable?.(Boolean(trafficRef.current));
        map.fitBounds(new g.maps.LatLngBounds({ lat: south, lng: west }, { lat: north, lng: east }));
        // Watchdog: a map that never draws a tile is not "rendered", whatever the cause. Fall back instead of
        // leaving a blank panel.
        tilesTimer = window.setTimeout(() => failed(
          // If Google already named a key/configuration problem, say so: retrying cannot fix it.
          authFailure ?? (lastGoogleCode && GOOGLE_ERRORS[lastGoogleCode] ? describeAuthFailure() : {
            reason: "Google Maps did not draw any map tiles within 15 seconds. Check the browser console for a Google Maps error.",
            code: lastGoogleCode, retryable: true,
          })), TILES_TIMEOUT_MS);
        listeners = [
          map.addListener("tilesloaded", () => window.clearTimeout(tilesTimer)),
          map.addListener("click", (event: GoogleApi) => {
            const pick = latest.current.onPick;
            if (event.latLng && pick) pick([Number(event.latLng.lat().toFixed(6)), Number(event.latLng.lng().toFixed(6))]);
          }),
          map.addListener("idle", () => {
            if (!latest.current.showGraph || map.getZoom() < 15 || graphLoadingRef.current) return;
            const visible = map.getBounds();
            if (!visible) return;
            const sw = visible.getSouthWest();
            const ne = visible.getNorthEast();
            const key = [sw.lat(), sw.lng(), ne.lat(), ne.lng()].map((v) => v.toFixed(4)).join(",");
            if (graphKeyRef.current === key) return;
            graphKeyRef.current = key;
            graphLoadingRef.current = true;
            publicApi.segments(sw.lat(), sw.lng(), ne.lat(), ne.lng())
              .then((result) => {
                graphOverlayRef.current.forEach((item) => item.setMap(null));
                graphOverlayRef.current = result.segments.map((segment) => new g.maps.Polyline({
                  map, path: path(segment.geometry), strokeColor: "#cfc2ff", strokeOpacity: 0.55, strokeWeight: 1.5, clickable: false,
                }));
                setGraphNote(result.truncated ? "Showing the first 3,000 segments" : null);
              })
              .catch(() => setGraphNote("Could not load road graph segments"))
              .finally(() => { graphLoadingRef.current = false; });
          }),
        ];
        setReady(true);
      })
      .catch((f: GoogleMapsFailure) => failed(f));
    return () => {
      cancelled = true;
      failureListeners.delete(failed);
      window.clearTimeout(tilesTimer);
      listeners.forEach((l) => l?.remove?.());
      infoWindowRef.current?.close();
      trafficRef.current?.setMap(null);
      trafficRef.current = null;
      overlayRef.current.forEach((item) => item.setMap(null));
      graphOverlayRef.current.forEach((item) => item.setMap(null));
      overlayRef.current = [];
      graphOverlayRef.current = [];
      mapRef.current = null;
    };
    // One map per mount; overlays, focus and traffic are applied by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  // Show or hide the visual traffic layer without rebuilding the map or its overlays.
  useEffect(() => {
    if (!ready || !trafficRef.current) return;
    trafficRef.current.setMap(showTraffic ? mapRef.current : null);
  }, [ready, showTraffic]);

  // Overlays are rebuilt only when their CONTENT changes (pages re-render on every poll with new arrays),
  // so there is no flicker and never a duplicate: everything drawn is tracked and removed first.
  const overlayKey = useMemo(() => JSON.stringify([
    city.coverage_bbox, zones.map((z) => [z.id, z.status]), hazard, closures.map((c) => [c.segment_id, c.kind, c.geometry]),
    routes.map((r) => [r.id, r.tone, Boolean(r.alternative), r.geometry]), pois.map((p) => [p.id, p.kind, p.lat, p.lon]),
    signals.map((s) => [s.id, s.status]), markers.map((m) => [m.id, m.lat, m.lon, m.color, m.radius, m.label, m.detail]), picked,
  ]), [city.coverage_bbox, zones, hazard, closures, routes, pois, signals, markers, picked]);

  useEffect(() => {
    const map = mapRef.current;
    const g = window.google;
    if (!ready || !map || !g?.maps) return;
    const p = latest.current;
    overlayRef.current.forEach((item) => item.setMap(null));
    overlayRef.current = [];
    const add = (item: GoogleApi) => overlayRef.current.push(item);
    const [south, west, north, east] = p.city.coverage_bbox;
    add(new g.maps.Rectangle({ map, bounds: { south, west, north, east }, strokeColor: "#b4a2fb", strokeOpacity: 0.8,
      strokeWeight: 1, fillOpacity: 0, clickable: false }));
    (p.zones ?? []).forEach((zone) => add(new g.maps.Rectangle({
      map, bounds: { south: zone.bounds[0], west: zone.bounds[1], north: zone.bounds[2], east: zone.bounds[3] },
      strokeColor: zone.status === "energized" ? "#b4a2fb" : "#ff7186", strokeOpacity: 0.8, strokeWeight: 1,
      fillColor: zone.status === "energized" ? "#b4a2fb" : "#ff7186", fillOpacity: zone.status === "energized" ? 0.03 : 0.12, clickable: false,
    })));
    (p.hazard ?? []).forEach((geometry) => add(new g.maps.Polyline({ map, path: path(geometry), strokeColor: COLORS.hazard,
      strokeOpacity: 0.35, strokeWeight: 9, clickable: false })));
    (p.closures ?? []).forEach((closure) => add(new g.maps.Polyline({
      map, path: path(closure.geometry), strokeColor: closure.kind === "closed" ? COLORS.closed : COLORS.caution,
      strokeOpacity: 0.95, strokeWeight: 5, clickable: false,
    })));
    // Routes: a dark casing under a high-contrast core, above Google's traffic lines (zIndex). Google
    // polylines have no dash option, so patterned routes are drawn as repeated line symbols.
    const pattern = (dash: string | undefined, color: string, weight: number) => {
      if (!dash) return undefined;
      const [on, off] = dash.split(" ").map(Number);
      return [{ icon: { path: `M 0,0 0,${Math.max(on, 0.5)}`, strokeOpacity: 1, strokeColor: color, strokeWeight: weight, scale: 1 },
        offset: "0", repeat: `${on + off}px` }];
    };
    const drawable = (p.routes ?? []).filter((r) => r.geometry && r.geometry.length >= 2);
    drawable.filter((r) => r.alternative).forEach((route) => add(new g.maps.Polyline({
      map, path: path(route.geometry), strokeOpacity: 0, strokeWeight: 3, clickable: false, zIndex: 19,
      icons: pattern(ALT_DASH, COLORS[route.tone], 3),
    })));
    drawable.filter((r) => !r.alternative).forEach((route) => {
      add(new g.maps.Polyline({ map, path: path(route.geometry), strokeColor: COLORS.casing, strokeOpacity: 0.85,
        strokeWeight: CASING_WIDTH, clickable: false, zIndex: 20 }));
      const dash = ROUTE_DASH[route.tone];
      add(new g.maps.Polyline({ map, path: path(route.geometry), strokeColor: COLORS[route.tone], strokeOpacity: dash ? 0 : 1,
        strokeWeight: ROUTE_WIDTH, clickable: false, zIndex: 21, icons: pattern(dash, COLORS[route.tone], ROUTE_WIDTH) }));
    });
    (p.pois ?? []).forEach((poi) => add(new g.maps.Marker({
      map, position: { lat: poi.lat, lng: poi.lon }, title: poiTitle(poi), zIndex: 1,
      label: poi.kind === "hospital" ? undefined : { text: POI_GLYPH[poi.kind], color: "#07060b", fontSize: "10px", fontWeight: "700" },
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: poi.kind === "hospital" ? 5 : 8, fillColor: COLORS.poi[poi.kind] ?? "#c9c5d6",
        fillOpacity: 0.95, strokeColor: "#07060b", strokeWeight: 1.5 },
    })));
    (p.signals ?? []).forEach((signal) => add(new g.maps.Marker({
      map, position: { lat: signal.lat, lng: signal.lon }, title: `${signal.name}: ${signal.status}. Location from OSM; power model simulated.`,
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: 6, fillColor: signal.status === "operational" ? COLORS.signalOk : COLORS.signalDark,
        fillOpacity: 0.95, strokeColor: "#0c0a12", strokeWeight: 2 },
    })));
    const info = infoWindowRef.current ?? new g.maps.InfoWindow();
    infoWindowRef.current = info;
    info.close(); // never leave a popup anchored to a marker that was just removed (e.g. a resolved incident)
    (p.markers ?? []).forEach((marker) => {
      const item = new g.maps.Marker({ map, position: { lat: marker.lat, lng: marker.lon }, zIndex: 30,
        title: marker.detail ? `${marker.label}: ${marker.detail}` : marker.label,
        icon: { path: g.maps.SymbolPath.CIRCLE, scale: marker.radius ? Math.min(13, Math.max(6, marker.radius / 1.4)) : 8,
          fillColor: marker.color ?? COLORS.incident, fillOpacity: 0.95, strokeColor: "#ffffff", strokeWeight: 2.5 } });
      item.addListener("click", () => {
        // Look the marker up at click time so the handler is the current one, not the one from the last rebuild.
        const current = latest.current.markers?.find((m) => m.id === marker.id);
        current?.onClick?.();
        if (current?.detail) {
          const content = document.createElement("div");
          content.textContent = `${current.label}: ${current.detail}`;
          content.style.color = "#161320";
          content.style.padding = "4px 6px";
          info.setContent(content);
          info.open({ map, anchor: item });
        }
      });
      add(item);
    });
    if (p.picked) add(new g.maps.Marker({ map, position: { lat: p.picked[0], lng: p.picked[1] }, title: "Reported location", zIndex: 31,
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: 10, fillColor: COLORS.incident, fillOpacity: 0.95, strokeColor: "#ffffff", strokeWeight: 3 } }));
  }, [ready, overlayKey]);

  // The camera moves only when the focus itself changes, never on a routine re-render, so the operator
  // can pan and zoom without being snapped back every few seconds.
  const focusKey = focus ? `${focus.length}:${focus[0]?.join(",")}:${focus[focus.length - 1]?.join(",")}` : "none";
  useEffect(() => {
    const map = mapRef.current;
    const g = window.google;
    if (!ready || !map || !g?.maps || !focus?.length) return;
    if (focus.length > 1) {
      const bounds = new g.maps.LatLngBounds();
      focus.forEach(([lat, lng]) => bounds.extend({ lat, lng }));
      map.fitBounds(bounds, 36);
    } else {
      map.setCenter({ lat: focus[0][0], lng: focus[0][1] });
      map.setZoom(16);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focusKey]);

  return (
    <div className="relative h-full w-full">
      <div ref={rootRef} className="h-full w-full" aria-label="Google Maps" />
      {!ready && <div className="absolute inset-0 grid place-items-center bg-ink-900/80 text-xs text-fog-400" role="status">Loading Google Maps…</div>}
      {graphNote && <div className="absolute left-2 top-16 rounded-md bg-ink-900/90 px-2 py-1 text-[11px] text-fog-300">{graphNote}</div>}
    </div>
  );
}
