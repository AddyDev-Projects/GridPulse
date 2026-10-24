import { useEffect, useRef, useState } from "react";
import { publicApi } from "../../services/api";
import type { CityMapProps } from "./CityMap";

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
}: CityMapProps & { apiKey: string; onFailure: (reason: string) => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<GoogleApi>(null);
  const overlayRef = useRef<GoogleApi[]>([]);
  const graphOverlayRef = useRef<GoogleApi[]>([]);
  const infoWindowRef = useRef<GoogleApi>(null);
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
    let trafficLayer: GoogleApi;
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
          ],
        });
        mapRef.current = map;
        if (import.meta.env.VITE_GOOGLE_MAPS_TRAFFIC_LAYER === "true" && g.maps.TrafficLayer) {
          trafficLayer = new g.maps.TrafficLayer();
          trafficLayer.setMap(map);
        }
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
      trafficLayer?.setMap(null);
      overlayRef.current.forEach((item) => item.setMap(null));
      graphOverlayRef.current.forEach((item) => item.setMap(null));
      overlayRef.current = [];
      graphOverlayRef.current = [];
      mapRef.current = null;
    };
    // The map is intentionally initialized once per provider attempt. Overlay updates are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

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
    routes.forEach((route) => overlayRef.current.push(new g.maps.Polyline({
      map, path: path(route.geometry), strokeColor: route.tone === "route" ? "#4ade9b" : route.tone === "routeAlt" ? "#7cc7ff" : "#ff7186",
      strokeOpacity: 0.95, strokeWeight: 5, strokeDasharray: route.tone === "conflict" ? [10, 8] : undefined, clickable: false,
    })));
    pois.forEach((poi) => overlayRef.current.push(new g.maps.Marker({
      map, position: { lat: poi.lat, lng: poi.lon }, title: `${poi.name} (${poi.kind.replace("_", " ")}, OpenStreetMap)`,
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: 5, fillColor: poi.kind === "hospital" ? "#7cc7ff" : poi.kind === "fire_station" ? "#ff9f6b" : "#cfc2ff", fillOpacity: 0.9, strokeColor: "#0c0a12", strokeWeight: 1 },
    })));
    signals.forEach((signal) => overlayRef.current.push(new g.maps.Marker({
      map, position: { lat: signal.lat, lng: signal.lon }, title: `${signal.name}: ${signal.status}. Location from OSM; power model simulated.`,
      icon: { path: g.maps.SymbolPath.CIRCLE, scale: 6, fillColor: signal.status === "operational" ? "#4ade9b" : "#ff7186", fillOpacity: 0.95, strokeColor: "#0c0a12", strokeWeight: 2 },
    })));
    const info = infoWindowRef.current ?? new g.maps.InfoWindow();
    infoWindowRef.current = info;
    markers.forEach((marker) => {
      const item = new g.maps.Marker({ map, position: { lat: marker.lat, lng: marker.lon }, title: marker.detail ? `${marker.label}: ${marker.detail}` : marker.label,
        icon: { path: g.maps.SymbolPath.CIRCLE, scale: marker.radius ? Math.min(13, Math.max(6, marker.radius / 1.4)) : 8, fillColor: marker.color ?? "#f3b8f5", fillOpacity: 0.9, strokeColor: "#ffffff", strokeWeight: 2 } });
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
    if (picked) overlayRef.current.push(new g.maps.Marker({ map, position: { lat: picked[0], lng: picked[1] }, title: "Reported location", icon: { path: g.maps.SymbolPath.CIRCLE, scale: 10, fillColor: "#f3b8f5", fillOpacity: 0.95, strokeColor: "#ffffff", strokeWeight: 3 } }));

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
