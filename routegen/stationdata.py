"""
Station / platform data acquisition and geometry helpers shared by
the STB, PTB and RTB exporters.

The main railway download (osm.py) only fetches elements tagged
``railway=*``. Stations and platforms are frequently mapped with
``public_transport=*`` only, and stop-area relations (which tie
stations to platforms) are never downloaded, so this module makes its
own small Overpass request and caches it under data/stations/.
"""

import json
import math
import time
from pathlib import Path

import numpy as np
import requests

from routegen.osm import OVERPASS_SERVERS


ROOT = Path(__file__).resolve().parent.parent

STATION_DATA_DIRECTORY = ROOT / "data" / "stations"

HEADERS = {
    "User-Agent": "TrainGo-RouteGen/0.1"
}

REQUEST_TIMEOUT = 180
MAX_RETRIES_PER_SERVER = 2

EARTH_RADIUS_M = 6371000.0
METRES_PER_DEG_LAT = 110574.0
METRES_PER_DEG_LON_EQUATOR = 111320.0

# OSM station=* values that are not main-line railway stations.
EXCLUDED_STATION_TYPES = {
    "subway",
    "light_rail",
    "tram",
    "monorail",
    "funicular",
    "miniature",
}


# ============================================================
# Geometry
# ============================================================

def haversine_m(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(dlambda / 2.0) ** 2
    )

    a = min(1.0, max(0.0, a))

    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def haversine_array(lat1, lon1, lat2, lon2):
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)

    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = (
        np.sin(dphi / 2.0) ** 2
        + np.cos(phi1)
        * np.cos(phi2)
        * np.sin(dlambda / 2.0) ** 2
    )

    a = np.clip(a, 0.0, 1.0)

    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def polyline_length_m(points):
    total = 0.0

    for index in range(len(points) - 1):
        total += haversine_m(
            points[index][0],
            points[index][1],
            points[index + 1][0],
            points[index + 1][1]
        )

    return total


def segment_projection(lat, lon, lats, lons):
    """
    Distance (m) and segment parameter t of the point to every
    segment of a polyline, in a local flat projection around the
    point.
    """

    kx = (
        METRES_PER_DEG_LON_EQUATOR
        * math.cos(math.radians(lat))
    )
    ky = METRES_PER_DEG_LAT

    ax = (lons[:-1] - lon) * kx
    ay = (lats[:-1] - lat) * ky
    bx = (lons[1:] - lon) * kx
    by = (lats[1:] - lat) * ky

    abx = bx - ax
    aby = by - ay

    length_squared = np.maximum(
        abx * abx + aby * aby,
        1e-9
    )

    t = np.clip(
        -(ax * abx + ay * aby) / length_squared,
        0.0,
        1.0
    )

    distance = np.hypot(
        ax + t * abx,
        ay + t * aby
    )

    return distance, t


def point_to_polyline_distance_m(lat, lon, lats, lons):
    if len(lats) == 0:
        return float("inf")

    if len(lats) == 1:
        return haversine_m(
            lat,
            lon,
            float(lats[0]),
            float(lons[0])
        )

    distance, _ = segment_projection(
        lat,
        lon,
        lats,
        lons
    )

    return float(distance.min())


class RouteLocator:
    """
    Projects arbitrary coordinates onto the generated route and
    reports offset and chainage (distance from the route start).
    """

    def __init__(self, path, nodes):
        coordinates = [
            (
                nodes[node_id]["latitude"],
                nodes[node_id]["longitude"]
            )
            for node_id in path
        ]

        if len(coordinates) < 2:
            raise ValueError(
                "RouteLocator needs a route of at least two nodes."
            )

        self.path = list(path)

        self.lats = np.array(
            [c[0] for c in coordinates],
            dtype=float
        )

        self.lons = np.array(
            [c[1] for c in coordinates],
            dtype=float
        )

        self.segment_lengths = haversine_array(
            self.lats[:-1],
            self.lons[:-1],
            self.lats[1:],
            self.lons[1:]
        )

        self.cumulative = np.concatenate(
            ([0.0], np.cumsum(self.segment_lengths))
        )

        self.length_m = float(self.cumulative[-1])

        self.node_chainage = {}

        for node_id, chainage in zip(
            self.path,
            self.cumulative
        ):
            self.node_chainage.setdefault(
                node_id,
                float(chainage)
            )

    def locate(self, latitude, longitude):
        distance, t = segment_projection(
            latitude,
            longitude,
            self.lats,
            self.lons
        )

        index = int(distance.argmin())
        fraction = float(t[index])

        return {
            "offset_m": float(distance[index]),
            "chainage_m": float(
                self.cumulative[index]
                + fraction * self.segment_lengths[index]
            ),
            "segment_from_node": self.path[index],
            "segment_to_node": self.path[index + 1],
            "nearest_node_id": (
                self.path[index]
                if fraction < 0.5
                else self.path[index + 1]
            )
        }


# ============================================================
# Overpass acquisition
# ============================================================

def build_station_query(south, west, north, east):
    bbox = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"

    return f"""
[out:json][timeout:180];

(
  nwr["railway"~"^(station|halt|platform)$"]({bbox});
  nwr["public_transport"~"^(station|platform)$"]["train"="yes"]({bbox});
  nwr["public_transport"="station"]["railway"]({bbox});
  relation["public_transport"="stop_area"]({bbox});
);

out body geom;
"""


def station_cache_path(south, west, north, east):
    return STATION_DATA_DIRECTORY / (
        f"stations_S{south:.6f}_W{west:.6f}"
        f"_N{north:.6f}_E{east:.6f}.json"
    )


def download_station_data(
    south,
    west,
    north,
    east,
    use_cache=True
):
    cache_file = station_cache_path(
        south,
        west,
        north,
        east
    )

    if use_cache and cache_file.exists():
        print(f"Station data cached: {cache_file.name}")

        with open(cache_file, "r", encoding="utf-8") as file:
            return json.load(file)

    query = build_station_query(
        south,
        west,
        north,
        east
    )

    print("Downloading station and platform data...")

    last_error = None

    for server_url in OVERPASS_SERVERS:

        for attempt in range(1, MAX_RETRIES_PER_SERVER + 1):

            try:
                response = requests.post(
                    server_url,
                    data=query.encode("utf-8"),
                    headers=HEADERS,
                    timeout=REQUEST_TIMEOUT
                )

                response.raise_for_status()

                data = response.json()

                STATION_DATA_DIRECTORY.mkdir(
                    parents=True,
                    exist_ok=True
                )

                with open(
                    cache_file,
                    "w",
                    encoding="utf-8"
                ) as file:
                    json.dump(
                        data,
                        file,
                        ensure_ascii=False
                    )

                print("✓ Station data received")

                return data

            except (
                requests.RequestException,
                ValueError
            ) as error:
                last_error = error

                print(f"✗ Station request failed: {error}")

                if attempt < MAX_RETRIES_PER_SERVER:
                    time.sleep(3)

    raise RuntimeError(
        "All Overpass servers failed for station data.\n"
        f"Last error: {last_error}"
    )


# ============================================================
# Parsing
# ============================================================

def _element_points(element):
    element_type = element.get("type")

    if element_type == "node":
        if "lat" in element and "lon" in element:
            return [(element["lat"], element["lon"])]
        return []

    points = []

    if element_type == "way":
        for item in element.get("geometry") or []:
            if item:
                points.append((item["lat"], item["lon"]))

    elif element_type == "relation":
        for member in element.get("members") or []:
            if (
                member.get("type") == "node"
                and "lat" in member
                and "lon" in member
            ):
                points.append((member["lat"], member["lon"]))

            for item in member.get("geometry") or []:
                if item:
                    points.append((item["lat"], item["lon"]))

    if not points:
        center = element.get("center")

        if center:
            points.append((center["lat"], center["lon"]))

    return points


def _centroid(points):
    pts = list(points)

    if len(pts) > 3 and pts[0] == pts[-1]:
        pts = pts[:-1]

    return (
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts)
    )


def parse_station_data(data):
    """
    Split a raw Overpass response into stations, platforms and
    stop areas.
    """

    stations = []
    platforms = []
    stop_areas = []

    for element in data.get("elements", []):

        tags = element.get("tags") or {}

        if not tags:
            continue

        element_type = element.get("type")
        element_id = element.get("id")

        railway = tags.get("railway", "")
        public_transport = tags.get("public_transport", "")

        if (
            element_type == "relation"
            and public_transport == "stop_area"
        ):
            stop_areas.append({
                "osm_type": element_type,
                "osm_id": element_id,
                "name": tags.get("name"),
                "members": [
                    {
                        "type": member.get("type"),
                        "ref": member.get("ref"),
                        "role": member.get("role", "")
                    }
                    for member in element.get("members") or []
                ],
                "tags": tags
            })
            continue

        points = _element_points(element)

        if not points:
            continue

        latitude, longitude = _centroid(points)

        record = {
            "osm_type": element_type,
            "osm_id": element_id,
            "key": f"{element_type}/{element_id}",
            "tags": tags,
            "points": points,
            "latitude": latitude,
            "longitude": longitude,
            "closed": (
                len(points) > 3 and points[0] == points[-1]
            )
        }

        if railway == "platform" or public_transport == "platform":
            platforms.append(record)

        elif (
            railway in {"station", "halt"}
            or public_transport == "station"
        ):
            if (
                tags.get("station", "").strip().lower()
                in EXCLUDED_STATION_TYPES
            ):
                continue

            stations.append(record)

    return {
        "stations": stations,
        "platforms": platforms,
        "stop_areas": stop_areas
    }


def load_station_data(south, west, north, east):
    data = download_station_data(
        south,
        west,
        north,
        east
    )

    parsed = parse_station_data(data)

    parsed["bounds"] = {
        "south": south,
        "west": west,
        "north": north,
        "east": east
    }

    return parsed
