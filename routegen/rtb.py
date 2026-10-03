"""
TrainGo Relationship Database (RTB)

The connectivity layer. It does not repeat the data held in the other
databases; it links them by ID:

    station  <-> platform      (stop area or proximity)
    platform <-> track         (TDB track IDs)
    station  <-> route / topology points / TDB tracks
    station  -> terrain tiles  (TEB) and scenery chunks (SDB)

and records which files make up the route.
"""

import math
from pathlib import Path

from routegen.sdb import (
    OSM_CHUNK_DIRECTORY,
    parse_osm_chunk_filename,
)
from routegen.serialize import write_database
from routegen.stationdata import haversine_m
from routegen.tdb import DEFAULT_TDB_FILE
from routegen.teb import (
    DEFAULT_TEB_FILE,
    dem_tile_name,
    latlon_to_tile,
)
from routegen.sdb import DEFAULT_SDB_FILE
from routegen.stb import DEFAULT_STB_FILE
from routegen.ptb import DEFAULT_PTB_FILE


RTB_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_RTB_FILE = ROOT / "output" / "relationships.rtb"

# Topology points within this chainage distance of a station are
# treated as part of its throat / approach.
STATION_TOPOLOGY_RADIUS_M = 500.0


def _relative(path):
    path = Path(path)

    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _cached_scenery_chunks():
    chunks = []

    if not OSM_CHUNK_DIRECTORY.exists():
        return chunks

    for path in OSM_CHUNK_DIRECTORY.glob("*.json"):
        bounds = parse_osm_chunk_filename(path)

        if bounds is not None:
            chunks.append((path.name, bounds))

    return chunks


def _scenery_chunks_for(latitude, longitude, chunks):
    return [
        name
        for name, (south, west, north, east) in chunks
        if south <= latitude <= north
        and west <= longitude <= east
    ]


def _terrain_references(latitude, longitude, teb):
    dem_name = dem_tile_name(
        math.floor(latitude),
        math.floor(longitude)
    )

    zoom = teb["satellite"]["zoom"]

    x, y = latlon_to_tile(latitude, longitude, zoom)

    files = {
        (tile["x"], tile["y"]): tile["file"]
        for tile in teb["satellite"]["tiles"]
    }

    known_dem = {tile["name"] for tile in teb["dem"]["tiles"]}

    return {
        "dem_tile": dem_name,
        "dem_tile_in_teb": dem_name in known_dem,
        "satellite_tile": {
            "zoom": zoom,
            "x": x,
            "y": y,
            "file": files.get((x, y)),
            "in_teb": (x, y) in files
        }
    }


def _track_ids_at_node(node_id, topology):
    way_ids = topology.get(
        "route_way_ids_by_node",
        {}
    ).get(node_id, set())

    return [f"T-{way_id}" for way_id in sorted(way_ids)]


def build_rtb(
    stb,
    ptb,
    tdb,
    teb,
    topology,
    locator,
    sdb_metadata=None
):
    stations = stb["stations"]
    platforms = ptb["platforms"]

    scenery_chunks = _cached_scenery_chunks()

    # --------------------------------------------------------
    # Pairwise relationships
    # --------------------------------------------------------

    station_platforms = []
    platform_tracks = []

    platforms_by_station = {}
    platform_tracks_by_station = {}

    for platform in platforms:

        link = platform["station"]

        if link:
            station_platforms.append({
                "station_id": link["station_id"],
                "platform_id": platform["platform_id"],
                "method": link["method"],
                "distance_m": link["distance_m"]
            })

            platforms_by_station.setdefault(
                link["station_id"],
                []
            ).append(platform["platform_id"])

        for track in platform["tracks"]:
            platform_tracks.append({
                "platform_id": platform["platform_id"],
                "track_id": track["track_id"],
                "distance_m": track["distance_m"],
                "confidence": track["confidence"]
            })

            if link:
                bucket = platform_tracks_by_station.setdefault(
                    link["station_id"],
                    []
                )

                if track["track_id"] not in bucket:
                    bucket.append(track["track_id"])

    # --------------------------------------------------------
    # Per-station links
    # --------------------------------------------------------

    topology_points = tdb.get("topology_points", [])

    station_links = []
    station_tracks = []

    for station in stations:

        route = station["route"]
        station_id = station["station_id"]

        near_points = []
        route_track_ids = []

        if route["on_route"]:

            route_track_ids = _track_ids_at_node(
                route["nearest_node_id"],
                topology
            )

            for point in topology_points:

                chainage = locator.node_chainage.get(
                    point["node_id"]
                )

                if chainage is None:
                    continue

                delta = chainage - route["chainage_m"]

                if abs(delta) <= STATION_TOPOLOGY_RADIUS_M:
                    near_points.append({
                        "node_id": point["node_id"],
                        "classification": point["classification"],
                        "chainage_offset_m": delta
                    })

            near_points.sort(
                key=lambda p: abs(p["chainage_offset_m"])
            )

        for track_id in platform_tracks_by_station.get(
            station_id,
            []
        ):
            station_tracks.append({
                "station_id": station_id,
                "track_id": track_id,
                "via": "platform"
            })

        for track_id in route_track_ids:
            station_tracks.append({
                "station_id": station_id,
                "track_id": track_id,
                "via": "route_node"
            })

        station_links.append({
            "station_id": station_id,
            "name": station["name"],
            "on_route": route["on_route"],
            "chainage_m": route["chainage_m"],
            "offset_m": route["offset_m"],
            "platform_ids": platforms_by_station.get(
                station_id,
                []
            ),
            "platform_track_ids": platform_tracks_by_station.get(
                station_id,
                []
            ),
            "route_track_ids": route_track_ids,
            "topology_points": near_points,
            "terrain": _terrain_references(
                station["latitude"],
                station["longitude"],
                teb
            ),
            "scenery_chunks": _scenery_chunks_for(
                station["latitude"],
                station["longitude"],
                scenery_chunks
            )
        })

    # --------------------------------------------------------
    # Route order
    # --------------------------------------------------------

    on_route = [s for s in stations if s["route"]["on_route"]]

    route_order = []
    previous = None

    for station in on_route:

        chainage = station["route"]["chainage_m"]

        route_order.append({
            "station_id": station["station_id"],
            "name": station["name"],
            "chainage_m": chainage,
            "distance_from_previous_m": (
                None
                if previous is None
                else chainage - previous
            )
        })

        previous = chainage

    # --------------------------------------------------------
    # References to the other databases
    # --------------------------------------------------------

    sdb_statistics = (sdb_metadata or {}).get("statistics", {})

    references = {
        "tdb": {
            "file": _relative(DEFAULT_TDB_FILE),
            "version": tdb.get("database_version"),
            "tracks": tdb.get("track_count")
        },
        "teb": {
            "file": _relative(DEFAULT_TEB_FILE),
            "version": teb.get("database_version"),
            "dem_tiles": len(teb["dem"]["tiles"]),
            "satellite_tiles": len(teb["satellite"]["tiles"]),
            "satellite_zoom": teb["satellite"]["zoom"]
        },
        "sdb": {
            "file": _relative(DEFAULT_SDB_FILE),
            "version": (sdb_metadata or {}).get("version"),
            "objects": sdb_statistics.get("total_objects")
        },
        "stb": {
            "file": _relative(DEFAULT_STB_FILE),
            "version": stb.get("database_version"),
            "stations": stb.get("station_count")
        },
        "ptb": {
            "file": _relative(DEFAULT_PTB_FILE),
            "version": ptb.get("database_version"),
            "platforms": ptb.get("platform_count")
        }
    }

    # --------------------------------------------------------
    # Consistency report
    # --------------------------------------------------------

    stations_without_platforms = [
        s["station_id"]
        for s in on_route
        if s["station_id"] not in platforms_by_station
    ]

    platforms_without_station = [
        p["platform_id"] for p in platforms if not p["station"]
    ]

    platforms_without_tracks = [
        p["platform_id"] for p in platforms if not p["tracks"]
    ]

    return {
        "database_type": "RTB",
        "database_version": RTB_VERSION,
        "route": {
            "start_node_id": locator.path[0],
            "end_node_id": locator.path[-1],
            "node_count": len(locator.path),
            "length_m": locator.length_m,
            "length_km": locator.length_m / 1000.0
        },
        "references": references,
        "route_order": route_order,
        "station_platforms": station_platforms,
        "platform_tracks": platform_tracks,
        "station_tracks": station_tracks,
        "station_links": station_links,
        "consistency": {
            "on_route_stations_without_platforms": (
                stations_without_platforms
            ),
            "platforms_without_station": platforms_without_station,
            "platforms_without_tracks": platforms_without_tracks
        },
        "statistics": {
            "stations": len(stations),
            "on_route_stations": len(on_route),
            "platforms": len(platforms),
            "station_platform_links": len(station_platforms),
            "platform_track_links": len(platform_tracks),
            "station_track_links": len(station_tracks),
            "stations_outside_terrain": sum(
                1
                for link in station_links
                if not link["terrain"]["satellite_tile"]["in_teb"]
            )
        }
    }


def export_rtb(
    stb,
    ptb,
    tdb,
    teb,
    topology,
    locator,
    sdb_metadata=None,
    output_path=DEFAULT_RTB_FILE
):
    database = build_rtb(
        stb,
        ptb,
        tdb,
        teb,
        topology,
        locator,
        sdb_metadata
    )

    write_database(
        output_path,
        "TRAIN_GO_RELATIONSHIP_DATABASE",
        database
    )

    return database
