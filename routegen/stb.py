"""
TrainGo Station Database (STB)

Station locations and station-level data, positioned against the
generated route (offset from the route and chainage along it).
"""

import re
from pathlib import Path

from routegen.serialize import write_database
from routegen.stationdata import haversine_m


STB_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_STB_FILE = ROOT / "output" / "stations.stb"

# Stations within this distance of the route are flagged as on-route.
ROUTE_STATION_MAX_OFFSET_M = 500.0

# OSM elements with the same name closer than this are one station
# (a station is often mapped as a node plus a building way plus a
# stop-area member).
STATION_MERGE_DISTANCE_M = 300.0

CODE_TAGS = (
    "ref",
    "railway:ref",
    "uic_ref",
    "ref:IR",
    "ref:station",
)

NAME_TAGS = (
    "alt_name",
    "old_name",
    "int_name",
    "official_name",
    "short_name",
)


def normalize_name(name):
    if not name:
        return ""

    text = name.casefold().strip()

    text = re.sub(
        r"\b(railway|rail)?\s*station\b",
        "",
        text
    )

    return re.sub(r"\s+", " ", text).strip()


def _priority(record):
    tags = record["tags"]

    railway = tags.get("railway", "")

    if railway == "station":
        tag_rank = 0
    elif railway == "halt":
        tag_rank = 1
    else:
        tag_rank = 2

    type_rank = {
        "node": 0,
        "way": 1,
        "relation": 2
    }.get(record["osm_type"], 3)

    return (tag_rank, type_rank, record["osm_id"])


def merge_stations(raw_stations):
    groups = []

    for record in sorted(raw_stations, key=_priority):

        key = normalize_name(
            record["tags"].get("name")
        )

        placed = False

        if key:
            for group in groups:
                primary = group["members"][0]

                if (
                    group["key"] == key
                    and haversine_m(
                        primary["latitude"],
                        primary["longitude"],
                        record["latitude"],
                        record["longitude"]
                    ) <= STATION_MERGE_DISTANCE_M
                ):
                    group["members"].append(record)
                    placed = True
                    break

        if not placed:
            groups.append({
                "key": key,
                "members": [record]
            })

    return groups


def _first_tag(members, keys):
    for member in members:
        for key in keys:
            value = member["tags"].get(key)

            if value:
                return value

    return None


def _collect_names(members):
    names = {}

    for member in members:
        for key, value in member["tags"].items():
            if key.startswith("name:") or key in NAME_TAGS:
                names.setdefault(key, value)

    return names


def build_station_record(group, locator):
    members = group["members"]
    primary = members[0]
    tags = primary["tags"]

    latitude = primary["latitude"]
    longitude = primary["longitude"]

    location = locator.locate(latitude, longitude)

    on_route = (
        location["offset_m"] <= ROUTE_STATION_MAX_OFFSET_M
    )

    return {
        "station_id": (
            f"ST-{primary['osm_type'][0].upper()}"
            f"{primary['osm_id']}"
        ),
        "name": tags.get("name"),
        "names": _collect_names(members),
        "code": _first_tag(members, CODE_TAGS),
        "railway": tags.get("railway"),
        "station_type": tags.get("station"),
        "operator": _first_tag(members, ("operator",)),
        "network": _first_tag(members, ("network",)),
        "wheelchair": _first_tag(members, ("wheelchair",)),
        "latitude": latitude,
        "longitude": longitude,
        "osm_elements": [m["key"] for m in members],
        "route": {
            "on_route": on_route,
            "offset_m": location["offset_m"],
            "chainage_m": location["chainage_m"],
            "chainage_km": location["chainage_m"] / 1000.0,
            "nearest_node_id": location["nearest_node_id"],
            "segment_from_node": location["segment_from_node"],
            "segment_to_node": location["segment_to_node"]
        },
        "tags": dict(tags)
    }


def build_stb(station_data, locator):
    records = [
        build_station_record(group, locator)
        for group in merge_stations(station_data["stations"])
    ]

    records.sort(
        key=lambda r: (
            not r["route"]["on_route"],
            r["route"]["chainage_m"]
            if r["route"]["on_route"]
            else 0.0,
            r["name"] or ""
        )
    )

    on_route = [r for r in records if r["route"]["on_route"]]

    return {
        "database_type": "STB",
        "database_version": STB_VERSION,
        "route_length_m": locator.length_m,
        "route_offset_threshold_m": ROUTE_STATION_MAX_OFFSET_M,
        "station_count": len(records),
        "stations": records,
        "statistics": {
            "total_stations": len(records),
            "on_route_stations": len(on_route),
            "named_stations": sum(1 for r in records if r["name"]),
            "stations_with_code": sum(
                1 for r in records if r["code"]
            ),
            "raw_osm_elements": len(station_data["stations"])
        }
    }


def export_stb(
    station_data,
    locator,
    output_path=DEFAULT_STB_FILE
):
    database = build_stb(station_data, locator)

    write_database(
        output_path,
        "TRAIN_GO_STATION_DATABASE",
        database
    )

    return database
