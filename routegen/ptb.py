"""
TrainGo Platform Database (PTB)

Platforms, the track(s) each one serves and the station each one
belongs to.
"""

import math
from pathlib import Path
from statistics import median

import numpy as np

from routegen.serialize import write_database
from routegen.stationdata import (
    haversine_m,
    point_to_polyline_distance_m,
    polyline_length_m,
)


PTB_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PTB_FILE = ROOT / "output" / "platforms.ptb"

# Only these TDB track roles can be served by a platform. The TDB also
# contains railway=platform ways and other non-track railway ways.
TRACK_ROLES = {"rail", "narrow_gauge", "light_rail", "preserved"}

PLATFORM_TRACK_MAX_M = 40.0
PLATFORM_TRACK_HIGH_M = 12.0
PLATFORM_TRACK_MEDIUM_M = 25.0
PLATFORM_MAX_TRACKS = 4

PLATFORM_STATION_MAX_M = 600.0
PLATFORM_ROUTE_MAX_OFFSET_M = 150.0

MAX_SAMPLE_POINTS = 8

ATTRIBUTE_TAGS = (
    "height",
    "surface",
    "covered",
    "shelter",
    "bench",
    "lit",
    "wheelchair",
    "tactile_paving",
    "level",
    "layer",
    "passenger_lines",
    "operator",
)


# ============================================================
# Track index
# ============================================================

def _prepare_tracks(tdb):
    tracks = []

    for track in tdb.get("tracks", []):

        if track.get("role") not in TRACK_ROLES:
            continue

        geometry = [
            point
            for point in track.get("geometry", [])
            if point.get("latitude") is not None
            and point.get("longitude") is not None
        ]

        if len(geometry) < 2:
            continue

        lats = np.array([p["latitude"] for p in geometry])
        lons = np.array([p["longitude"] for p in geometry])

        tracks.append({
            "track_id": track["track_id"],
            "osm_way_id": track.get("osm_way_id"),
            "lats": lats,
            "lons": lons,
            "south": float(lats.min()),
            "north": float(lats.max()),
            "west": float(lons.min()),
            "east": float(lons.max())
        })

    return tracks


def _sample_points(points):
    if len(points) <= MAX_SAMPLE_POINTS:
        return list(points)

    step = (len(points) - 1) / (MAX_SAMPLE_POINTS - 1)

    return [
        points[int(round(i * step))]
        for i in range(MAX_SAMPLE_POINTS)
    ]


def find_serving_tracks(platform, tracks):
    """
    Tracks within PLATFORM_TRACK_MAX_M of the platform, nearest first.

    The distance is the median over sampled platform vertices so a
    track that merely touches one end of a long platform does not
    count as serving it.
    """

    points = platform["points"]

    lat_margin = PLATFORM_TRACK_MAX_M / 110574.0

    lon_margin = lat_margin / max(
        math.cos(math.radians(platform["latitude"])),
        0.01
    )

    south = min(p[0] for p in points) - lat_margin
    north = max(p[0] for p in points) + lat_margin
    west = min(p[1] for p in points) - lon_margin
    east = max(p[1] for p in points) + lon_margin

    samples = _sample_points(points)

    results = []

    for track in tracks:

        if (
            track["north"] < south
            or track["south"] > north
            or track["east"] < west
            or track["west"] > east
        ):
            continue

        distance = median(
            point_to_polyline_distance_m(
                lat,
                lon,
                track["lats"],
                track["lons"]
            )
            for lat, lon in samples
        )

        if distance <= PLATFORM_TRACK_MAX_M:
            results.append((distance, track))

    results.sort(key=lambda item: item[0])

    serving = []

    for distance, track in results[:PLATFORM_MAX_TRACKS]:

        if distance <= PLATFORM_TRACK_HIGH_M:
            confidence = "HIGH"
        elif distance <= PLATFORM_TRACK_MEDIUM_M:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        serving.append({
            "track_id": track["track_id"],
            "osm_way_id": track["osm_way_id"],
            "distance_m": distance,
            "confidence": confidence
        })

    return serving


# ============================================================
# Station association
# ============================================================

def _stop_area_station_map(station_data, station_by_element):
    result = {}

    for area in station_data.get("stop_areas", []):

        keys = [
            f"{m['type']}/{m['ref']}"
            for m in area["members"]
        ]

        station_ids = [
            station_by_element[key]
            for key in keys
            if key in station_by_element
        ]

        if not station_ids:
            continue

        for key in keys:
            result.setdefault(key, station_ids[0])

    return result


def _nearest_station(platform, stations):
    best = None
    best_distance = PLATFORM_STATION_MAX_M

    for station in stations:
        distance = haversine_m(
            platform["latitude"],
            platform["longitude"],
            station["latitude"],
            station["longitude"]
        )

        if distance <= best_distance:
            best = station
            best_distance = distance

    if best is None:
        return None

    return {
        "station_id": best["station_id"],
        "method": "proximity",
        "distance_m": best_distance
    }


# ============================================================
# Build
# ============================================================

def build_platform_record(
    platform,
    tracks,
    station_link,
    locator
):
    tags = platform["tags"]

    is_relation = platform["osm_type"] == "relation"

    length = (
        None
        if is_relation or len(platform["points"]) < 2
        else polyline_length_m(platform["points"])
    )

    location = locator.locate(
        platform["latitude"],
        platform["longitude"]
    )

    serving = find_serving_tracks(platform, tracks)

    return {
        "platform_id": (
            f"PL-{platform['osm_type'][0].upper()}"
            f"{platform['osm_id']}"
        ),
        "osm_element": platform["key"],
        "name": tags.get("name"),
        "ref": tags.get("ref") or tags.get("local_ref"),
        "latitude": platform["latitude"],
        "longitude": platform["longitude"],
        "length_m": length,
        "is_area": platform["closed"],
        "attributes": {
            key: tags[key]
            for key in ATTRIBUTE_TAGS
            if key in tags
        },
        "station": station_link,
        "tracks": serving,
        "route": {
            "on_route": (
                location["offset_m"]
                <= PLATFORM_ROUTE_MAX_OFFSET_M
            ),
            "offset_m": location["offset_m"],
            "chainage_m": location["chainage_m"],
            "nearest_node_id": location["nearest_node_id"]
        },
        "geometry": (
            []
            if is_relation
            else [
                {"latitude": lat, "longitude": lon}
                for lat, lon in platform["points"]
            ]
        ),
        "tags": dict(tags)
    }


def build_ptb(station_data, stb, tdb, locator):
    stations = stb["stations"]

    station_by_element = {
        key: station["station_id"]
        for station in stations
        for key in station["osm_elements"]
    }

    area_map = _stop_area_station_map(
        station_data,
        station_by_element
    )

    tracks = _prepare_tracks(tdb)

    records = []

    for platform in station_data["platforms"]:

        station_link = None

        if platform["key"] in area_map:
            station_id = area_map[platform["key"]]

            station = next(
                s for s in stations
                if s["station_id"] == station_id
            )

            station_link = {
                "station_id": station_id,
                "method": "stop_area",
                "distance_m": haversine_m(
                    platform["latitude"],
                    platform["longitude"],
                    station["latitude"],
                    station["longitude"]
                )
            }

        else:
            station_link = _nearest_station(platform, stations)

        records.append(
            build_platform_record(
                platform,
                tracks,
                station_link,
                locator
            )
        )

    records.sort(
        key=lambda r: (
            not r["route"]["on_route"],
            r["route"]["chainage_m"],
            r["ref"] or ""
        )
    )

    return {
        "database_type": "PTB",
        "database_version": PTB_VERSION,
        "platform_count": len(records),
        "platforms": records,
        "statistics": {
            "total_platforms": len(records),
            "on_route_platforms": sum(
                1 for r in records if r["route"]["on_route"]
            ),
            "platforms_with_station": sum(
                1 for r in records if r["station"]
            ),
            "platforms_with_tracks": sum(
                1 for r in records if r["tracks"]
            ),
            "platforms_without_station": sum(
                1 for r in records if not r["station"]
            ),
            "platforms_without_tracks": sum(
                1 for r in records if not r["tracks"]
            )
        }
    }


def export_ptb(
    station_data,
    stb,
    tdb,
    locator,
    output_path=DEFAULT_PTB_FILE
):
    database = build_ptb(
        station_data,
        stb,
        tdb,
        locator
    )

    write_database(
        output_path,
        "TRAIN_GO_PLATFORM_DATABASE",
        database
    )

    return database
