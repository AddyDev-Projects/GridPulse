"""Build GridPulse's Mangaluru road graph from OpenStreetMap (via the Overpass API).

This runs offline as a maintenance command (`python -m app.cli geodata fetch`), never per request.
The result is a versioned, checksummed file under backend/data/mangaluru/ that the backend loads
at startup. OSM data is (c) OpenStreetMap contributors, available under the ODbL 1.0.

Graph construction
  * Ways with a routable `highway` tag inside the bounding box; `access=no/private` excluded.
  * Graph nodes are OSM nodes where ways meet, way endpoints, and traffic-signal nodes.
  * Each way is split into segments between graph nodes; the full geometry is kept.
  * Directionality: oneway=yes/true/1, -1, junction=roundabout and motorways are honoured.
  * Weight: estimated free-flow travel time from `maxspeed` or a per-class default speed. This is
    NOT live traffic.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "GridPulse/0.2 (disaster-response simulation; offline geodata build)"

# Central Mangaluru (south, west, north, east). Reports outside this box are outside coverage.
MANGALURU_BBOX = (12.835, 74.815, 12.935, 74.895)
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "mangaluru"
GRAPH_FILE = DATA_DIR / "mangaluru_roads.json.gz"

ROUTABLE = (
    "motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|"
    "tertiary|tertiary_link|unclassified|residential|living_street|road"
)
DEFAULT_SPEED_KMH = {
    "motorway": 80, "trunk": 60, "primary": 50, "secondary": 40, "tertiary": 35, "unclassified": 30,
    "residential": 25, "living_street": 15, "road": 25,
}
POI_KINDS = ("hospital", "fire_station", "police")


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:180];
(
  way["highway"~"^({ROUTABLE})$"]["area"!="yes"]({b});
)->.roads;
.roads out body;
.roads >;
out skel qt;
node["highway"="traffic_signals"]({b});
out body;
nwr["amenity"~"^({'|'.join(POI_KINDS)})$"]({b});
out tags center;
"""


def fetch_overpass(bbox: tuple[float, float, float, float], timeout: float = 240.0) -> dict[str, Any]:
    r = httpx.post(OVERPASS_URL, data={"data": overpass_query(bbox)}, timeout=timeout,
                   headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _speed_kmh(tags: dict[str, str]) -> float:
    raw = tags.get("maxspeed", "")
    try:
        value = float(raw.split()[0])
        if "mph" in raw:
            value *= 1.609
        if 5 <= value <= 130:
            return value
    except (ValueError, IndexError):
        pass
    hw = tags.get("highway", "road")
    return float(DEFAULT_SPEED_KMH.get(hw.removesuffix("_link"), 30 if hw.endswith("_link") else 25))


def _direction(tags: dict[str, str]) -> int:
    """1 = forward only, -1 = reverse only, 0 = both ways."""
    ow = tags.get("oneway", "").lower()
    if ow in ("yes", "true", "1"):
        return 1
    if ow == "-1":
        return -1
    if ow in ("no", "false", "0"):
        return 0
    if tags.get("junction") in ("roundabout", "circular") or tags.get("highway") in ("motorway", "motorway_link"):
        return 1
    return 0


def build_graph(osm: dict[str, Any], bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    coords: dict[int, tuple[float, float]] = {}
    signals: set[int] = set()
    ways: list[dict[str, Any]] = []
    pois: list[dict[str, Any]] = []
    for el in osm["elements"]:
        tags = el.get("tags", {})
        if el["type"] == "node":
            coords[el["id"]] = (el["lat"], el["lon"])
            if tags.get("highway") == "traffic_signals":
                signals.add(el["id"])
        if tags.get("amenity") in POI_KINDS:
            c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
            if c:
                pois.append({"id": f"{el['type'][0]}{el['id']}", "kind": tags["amenity"], "name": tags.get("name"),
                             "lat": round(c["lat"], 7), "lon": round(c["lon"], 7)})
        if el["type"] == "way" and "highway" in tags:
            if tags.get("access") in ("no", "private") or tags.get("motor_vehicle") == "no":
                continue
            ways.append(el)

    usage: Counter[int] = Counter()
    for w in ways:
        usage.update(set(w["nodes"]))
    graph_nodes: set[int] = set()
    for w in ways:
        nodes = w["nodes"]
        graph_nodes.update((nodes[0], nodes[-1]))
        seen: set[int] = set()
        for n in nodes:
            if usage[n] > 1 or n in signals or n in seen:
                graph_nodes.add(n)
            seen.add(n)

    segments: list[dict[str, Any]] = []
    for w in ways:
        tags = w["tags"]
        nodes = [n for n in w["nodes"] if n in coords]
        if len(nodes) < 2:
            continue
        speed = _speed_kmh(tags)
        direction = _direction(tags)
        start, idx = 0, 0
        for i in range(1, len(nodes)):
            if nodes[i] in graph_nodes or i == len(nodes) - 1:
                part = nodes[start : i + 1]
                if direction == -1:
                    part = part[::-1]
                geom = [coords[n] for n in part]
                length = sum(haversine_m(*geom[k], *geom[k + 1]) for k in range(len(geom) - 1))
                if length > 0:
                    segments.append({
                        "id": f"w{w['id']}s{idx}", "way": w["id"], "u": f"n{part[0]}", "v": f"n{part[-1]}",
                        "oneway": direction != 0, "highway": tags["highway"], "name": tags.get("name"),
                        "length_m": round(length, 1), "speed_kmh": speed,
                        "travel_s": round(length / (speed / 3.6), 1),
                        "geometry": [[round(a, 7), round(b, 7)] for a, b in geom],
                    })
                    idx += 1
                start = i

    used = {s["u"] for s in segments} | {s["v"] for s in segments}
    node_out = {f"n{n}": [round(coords[n][0], 7), round(coords[n][1], 7)] for n in graph_nodes if f"n{n}" in used}
    return {
        "nodes": node_out,
        "segments": segments,
        "signals": sorted(f"n{n}" for n in signals if f"n{n}" in node_out),
        "pois": sorted(pois, key=lambda p: p["id"]),
        "bbox": list(bbox),
    }


def content_sha256(graph: dict[str, Any]) -> str:
    body = {k: graph[k] for k in ("nodes", "segments", "signals", "pois", "bbox")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_graph_file(osm: dict[str, Any], bbox: tuple[float, float, float, float], path: Path = GRAPH_FILE) -> dict[str, Any]:
    graph = build_graph(osm, bbox)
    graph["meta"] = {
        "city": "Mangaluru", "state": "Karnataka", "country": "India", "country_code": "IN",
        "source": "OpenStreetMap via Overpass API",
        "license": "ODbL 1.0 (https://opendatacommons.org/licenses/odbl/)",
        "attribution": "© OpenStreetMap contributors",
        "osm_base_timestamp": osm.get("osm3s", {}).get("timestamp_osm_base"),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "builder": "backend/app/geo/osm.py",
        "weights": "Estimated free-flow travel time from maxspeed or per-class defaults; not live traffic.",
        "node_count": len(graph["nodes"]), "segment_count": len(graph["segments"]),
        "signal_count": len(graph["signals"]), "poi_count": len(graph["pois"]),
        "sha256": content_sha256(graph),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(graph, f, separators=(",", ":"), ensure_ascii=False)
    return graph["meta"]
