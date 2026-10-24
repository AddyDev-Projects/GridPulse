import { useEffect, useRef, useState } from "react";
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

let googleMapsPromise: Promise<GoogleApi> | null = null;

function loadGoogleMaps(apiKey: string): Promise<GoogleApi> {
  if (window.google?.maps) return Promise.resolve(window.google);
  if (googleMapsPromise) return googleMapsPromise;
  googleMapsPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.async = true;
    script.defer = true;
    script.dataset.gridpulseGoogleMaps = "true";
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&v=weekly`;
    const previousAuthFailure = window.gm_authFailure;
    let settled = false;
    const fail = (reason: Error) => {
      if (settled) return;
      settled = true;
      window.gm_authFailure = previousAuthFailure;
      reject(reason);
    };
    window.gm_authFailure = () => fail(new Error("Google Maps authorization, billing, quota, or key restriction failed."));
    script.onload = () => {
      if (window.google?.maps) {
        settled = true;
        window.gm_authFailure = previousAuthFailure;
        resolve(window.google);
      } else {
        fail(new Error("Google Maps loaded without its JavaScript API."));
      }
    };
    script.onerror = () => fail(new Error("Google Maps could not be loaded; using OpenStreetMap fallback."));
    document.head.appendChild(script);
  });
  return googleMapsPromise;
}

function path(points: [number, number][]) {
  return points.map(([lat, lng]) => ({ lat, lng }));
}

export function GoogleMapCanvas({
  apiKey,
  onFailure,
  city,
  className,
  markers = [],
  closures = [],
  routes = [],
  hazard = [],
  signals = [],
  zones = [],
  pois = [],
  showGraph = false,
  focus,
  picked,
  onPick,
  showTraffic = false,
  onTrafficAvailable,
}: CityMapProps & {
  apiKey: string;
  onFailure: (reason: string) => void;
  showTraffic?: boolean;
  onTrafficAvailable?: (available: boolean) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<GoogleApi>(null);
  const overlayRef = useRef<GoogleApi[]>([]);
  const graphOverlayRef = useRef<GoogleApi[]>([]);
  const infoWindowRef = useRef<GoogleApi>(null);
  const trafficRef = useRef<GoogleApi>(null);
  const onPickRef = useRef(onPick);
  const [ready, setReady] = useState(false);
  const [graphNote, setGraphNote] = useState<string | null>(null);
  const graphKeyRef = useRef<string | null>(null);
  const graphLoadingRef = useRef(false);

  useEffect(() => {
    onPickRef.current = onPick;
  }, [onPick]);

  useEffect(() => {
    let cancelled = false;
    let clickListener: GoogleApi;
    let idleListener: GoogleApi;
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
          gestureHandling: "greedy",
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
        onTrafficAvailable?.(Boolean(trafficRef.current));
        const bounds = new g.maps.LatLngBounds({ lat: south, lng: west }, { lat: north, lng: east });
        map.fitBounds(bounds);
        clickListener = map.addListener("click", (event: GoogleApi) => {
          if (event.latLng && onPickRef.current) onPickRef.current([Number(event.latLng.lat().toFixed(6)), Number(event.latLng.lng().toFixed(6))]);
        });
        idleListener = map.addListener("idle", () => {
          if (!showGraph || map.getZoom() < 15 || graphLoadingRef.current) return;
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
                map,
                path: path(segment.geometry),
                strokeColor: "#cfc2ff",
                strokeOpacity: 0.55,
                strokeWeight: 1.5,
                clickable: false,
              }));
              setGraphNote(result.truncated ? "Showing the first 3,000 segments" : null);
            })
            .catch(() => setGraphNote("Could not load road graph segments"))
            .finally(() => { graphLoadingRef.current = false; });
        });
        setReady(true);
      })
      .catch((error: Error) => {
        if (!cancelled) onFailure(error.message);
      });
    return () => {
      cancelled = true;
      clickListener?.remove();
      idleListener?.remove();
      infoWindowRef.current?.close();
      trafficRef.current?.setMap(null);
      trafficRef.current = null;
      overlayRef.current.forEach((item) => item.setMap(null));
      graphOverlayRef.current.forEach((item) => item.setMap(null));
      overlayRef.current = [];
      graphOverlayRef.current = [];
      mapRef.current = null;
    };
    // The map is intentionally initialized once per provider attempt. Overlay updates are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  // Show or hide the visual traffic layer without rebuilding the map or its overlays.
  useEffect(() => {
    if (!ready || !trafficRef.current) return;
    trafficRef.current.setMap(showTraffic ? mapRef.current : null);
  }, [ready, showTraffic]);

  useEffect(() => {
    const map = mapRef.current;
    const g = window.google;
    if (!ready || !map || !g?.maps) return;
    overlayRef.current.forEach((item) => item.setMap(null));
    overlayRef.current = [];
    const [south, west, north, east] = city.coverage_bbox;
    const coverage = new g.maps.Rectangle({
      map,
      bounds: { south, west, north, east },
      strokeColor: "#b4a2fb",
      strokeOpacity: 0.8,
      strokeWeight: 1,
      fillOpacity: 0,
      clickable: false,
    });
    overlayRef.current.push(coverage);
    zones.forEach((zone) => overlayRef.current.push(new g.maps.Rectangle({
      map,
      bounds: { south: zone.bounds[0], west: zone.bounds[1], north: zone.bounds[2], east: zone.bounds[3] },
      strokeColor: zone.status === "energized" ? "#b4a2fb" : "#ff7186",
      strokeOpacity: 0.8,
      strokeWeight: 1,
      fillColor: zone.status === "energized" ? "#b4a2fb" : "#ff7186",
      fillOpacity: zone.status === "energized" ? 0.03 : 0.12,
      clickable: false,
    })));
    hazard.forEach((geometry) => overlayRef.current.push(new g.maps.Polyline({ map, path: path(geometry), strokeColor: "#ff9f6b", strokeOpacity: 0.35, strokeWeight: 9, clickable: false })));
    closures.forEach((closure) => overlayRef.current.push(new g.maps.Polyline({
      map, path: path(closure.geometry), strokeColor: closure.kind === "closed" ? "#ff7186" : "#f6c453",
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
    routes.filter((r) => r.alternative).forEach((route) => overlayRef.current.push(new g.maps.Polyline({
      map, path: path(route.geometry), strokeOpacity: 0, strokeWeight: 3, clickable: false, zIndex: 19,
      icons: pattern(ALT_DASH, COLORS[route.tone], 3),
    })));
    routes.filter((r) => !r.alternative).forEach((route) => {
      overlayRef.current.push(new g.maps.Polyline({
        map, path: path(route.geometry), strokeColor: COLORS.casing, strokeOpacity: 0.85, strokeWeight: CASING_WIDTH,
        clickable: false, zIndex: 20,
      }));
      const dash = ROUTE_DASH[route.tone];
      overlayRef.current.push(new g.maps.Polyline({
        map, path: path(route.geometry), strokeColor: COLORS[route.tone], strokeOpacity: dash ? 0 : 1,
        strokeWeight: ROUTE_WIDTH, clickable: false, zIndex: 21, icons: pattern(dash, COLORS[route.tone], ROUTE_WIDTH),
      }));
    });
    pois.forEach((poi) => overlayRef.current.push(new g.maps.Marker({
      map, position: { lat: poi.lat, lng: poi.lon }, title: poiTitle(poi), zIndex: 1,
      label: poi.kind === "hospital" ? undefined : { text: POI_GLYPH[poi.kind], color: "#07060b", fontSize: "10px", fontWeight: "700" },
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: poi.kind === "hospital" ? 5 : 8, fillColor: COLORS.poi[poi.kind] ?? "#c9c5d6",
        fillOpacity: 0.95, strokeColor: "#07060b", strokeWeight: 1.5 },
    })));
    signals.forEach((signal) => overlayRef.current.push(new g.maps.Marker({
      map, position: { lat: signal.lat, lng: signal.lon }, title: `${signal.name}: ${signal.status}. Location from OSM; power model simulated.`,
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: 6, fillColor: signal.status === "operational" ? "#4ade9b" : "#ff7186", fillOpacity: 0.95, strokeColor: "#0c0a12", strokeWeight: 2 },
    })));
    const info = infoWindowRef.current ?? new g.maps.InfoWindow();
    infoWindowRef.current = info;
    info.close(); // never leave a popup anchored to a marker that was just removed (e.g. a resolved incident)
    markers.forEach((marker) => {
      const item = new g.maps.Marker({ map, position: { lat: marker.lat, lng: marker.lon }, zIndex: 30,
        title: marker.detail ? `${marker.label}: ${marker.detail}` : marker.label,
        icon: { path: g.maps.SymbolPath.CIRCLE, scale: marker.radius ? Math.min(13, Math.max(6, marker.radius / 1.4)) : 8, fillColor: marker.color ?? COLORS.incident, fillOpacity: 0.95, strokeColor: "#ffffff", strokeWeight: 2.5 } });
      if (marker.onClick || marker.detail) item.addListener("click", () => {
        marker.onClick?.();
        if (marker.detail) {
          const content = document.createElement("div");
          content.textContent = `${marker.label}: ${marker.detail}`;
          content.style.color = "#161320";
          content.style.padding = "4px 6px";
          info.setContent(content);
          info.open({ map, anchor: item });
        }
      });
      overlayRef.current.push(item);
    });
    if (picked) overlayRef.current.push(new g.maps.Marker({ map, position: { lat: picked[0], lng: picked[1] }, title: "Reported location", icon: { path: g.maps.SymbolPath.CIRCLE, scale: 10, fillColor: COLORS.incident, fillOpacity: 0.95, strokeColor: "#ffffff", strokeWeight: 3 } }));

    if (focus && focus.length > 1) {
      const focusBounds = new g.maps.LatLngBounds();
      focus.forEach(([lat, lng]) => focusBounds.extend({ lat, lng }));
      map.fitBounds(focusBounds, 36);
    } else if (focus && focus.length === 1) {
      map.setCenter({ lat: focus[0][0], lng: focus[0][1] });
      map.setZoom(16);
    }
  }, [city, closures, focus, hazard, markers, pois, ready, routes, signals, zones, picked]);

  return (
    <div className={className ?? "h-full"}>
      <div ref={rootRef} className="h-full w-full" aria-label="Google Maps" />
      {!ready && <div className="absolute inset-0 grid place-items-center bg-ink-900/80 text-xs text-fog-400">Loading Google Maps…</div>}
      {graphNote && <div className="absolute left-2 top-16 rounded-md bg-ink-900/90 px-2 py-1 text-[11px] text-fog-300">{graphNote}</div>}
    </div>
  );
}
