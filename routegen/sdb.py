from __future__ import annotations

import argparse
import csv
import gc
import gzip
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests


# ============================================================================
# TrainGo SDB
# Scenery Database Generator
#
# Cache formats
#
# OSM:
#   S22.249734_W87.384878_N22.299734_E87.434878.json
#
# GIS:
#   0a3b9b39bfd1b9f6.jsonl.gz
#
# OSM and GIS caches are intentionally handled independently.
# ============================================================================


SDB_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent.parent

SDB_DIRECTORY = ROOT / "output" / "scenery"

# Legacy / compatibility cache locations
OSM_CACHE_FILE = SDB_DIRECTORY / "osm_scenery.json"
GIS_CACHE_FILE = SDB_DIRECTORY / "gis_buildings.geojson"

# Actual chunk/partition caches
OSM_CHUNK_DIRECTORY = SDB_DIRECTORY / "osm_chunks"
GIS_PARTITION_DIRECTORY = SDB_DIRECTORY / "gis_partitions"

# Temporary build state
SDB_STAGE_DIRECTORY = SDB_DIRECTORY / ".stage"
SDB_STAGE_OBJECTS = SDB_STAGE_DIRECTORY / "objects.jsonl"
SDB_STAGE_DATABASE = SDB_STAGE_DIRECTORY / "build.sqlite"

DEFAULT_SDB_FILE = ROOT / "output" / "scenery.sdb"


# ============================================================================
# Configuration
# ============================================================================

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

GIS_DATASET_LINKS_URL = (
    "https://bfppub.blob.core.windows.net/$web/2026-08-13/dataset-links.csv"
)

REQUEST_TIMEOUT = 120
GIS_DOWNLOAD_TIMEOUT = 300

HEADERS = {
    "User-Agent": "TrainGo-RouteGen/0.1",
}

# OSM chunks are 0.05 x 0.05 degrees.
OSM_CHUNK_SIZE = 0.05

OSM_RETRY_ROUNDS = 3
OSM_RETRY_DELAY = 5
OSM_RETRY_MAX = 60
OSM_RETRY_JITTER = 2
OSM_RETRY_AFTER_MAX = 120
OSM_FAILED_CHUNK_RETRY_PASSES = 3

BUILDING_RECONCILIATION_DISTANCE_M = 4.0

METERS_PER_DEG_LAT = 110574.0
GRID_CELL_METERS = 8.0

PRINT_EVERY_GIS_BUILDINGS = 10000
PRINT_EVERY_OSM_OBJECTS = 10000


# ============================================================================
# OSM filename format
# ============================================================================

OSM_CHUNK_EXTENSION = ".json"

OSM_CHUNK_FILENAME_RE = re.compile(
    r"^"
    r"S(?P<south>-?\d+(?:\.\d{6})?)"
    r"_W(?P<west>-?\d+(?:\.\d{6})?)"
    r"_N(?P<north>-?\d+(?:\.\d{6})?)"
    r"_E(?P<east>-?\d+(?:\.\d{6})?)"
    r"\.json$"
)


# ============================================================================
# General helpers
# ============================================================================


def ensure_directories() -> None:
    SDB_DIRECTORY.mkdir(parents=True, exist_ok=True)
    OSM_CHUNK_DIRECTORY.mkdir(parents=True, exist_ok=True)
    GIS_PARTITION_DIRECTORY.mkdir(parents=True, exist_ok=True)
    SDB_STAGE_DIRECTORY.mkdir(parents=True, exist_ok=True)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_tags(tags: Any) -> Dict[str, str]:
    if not isinstance(tags, dict):
        return {}

    return {
        str(k): str(v)
        for k, v in tags.items()
        if v is not None
    }


def sanitize_filename(value: str) -> str:
    value = str(value)
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    return value.strip(" ._")


def expand_bounds(
    south: float,
    west: float,
    north: float,
    east: float,
    padding: float,
) -> Tuple[float, float, float, float]:

    return (
        south - padding,
        west - padding,
        north + padding,
        east + padding,
    )


def normalize_bounds(
    south: float,
    west: float,
    north: float,
    east: float,
) -> Tuple[float, float, float, float]:

    if south > north:
        south, north = north, south

    if west > east:
        west, east = east, west

    return south, west, north, east


# ============================================================================
# OSM chunk naming
# ============================================================================


def _osm_chunk_stem(
    south: float,
    west: float,
    north: float,
    east: float,
) -> str:

    south, west, north, east = normalize_bounds(
        south,
        west,
        north,
        east,
    )

    return (
        f"S{south:.6f}"
        f"_W{west:.6f}"
        f"_N{north:.6f}"
        f"_E{east:.6f}"
    )


def osm_chunk_filename(
    south: float,
    west: float,
    north: float,
    east: float,
) -> Path:

    return (
        OSM_CHUNK_DIRECTORY
        / f"{_osm_chunk_stem(south, west, north, east)}.json"
    )


def is_osm_chunk_filename(path: Path) -> bool:
    return OSM_CHUNK_FILENAME_RE.fullmatch(path.name) is not None


def parse_osm_chunk_filename(
    path: Path,
) -> Optional[Tuple[float, float, float, float]]:

    match = OSM_CHUNK_FILENAME_RE.fullmatch(path.name)

    if not match:
        return None

    return (
        float(match.group("south")),
        float(match.group("west")),
        float(match.group("north")),
        float(match.group("east")),
    )


def osm_chunk_cache_candidates(
    south: float,
    west: float,
    north: float,
    east: float,
) -> List[Path]:

    # Intentionally ONLY .json.
    return [
        osm_chunk_filename(
            south,
            west,
            north,
            east,
        )
    ]


def is_valid_osm_chunk_cache(path: Path) -> bool:
    """
    Lightweight validation.

    We deliberately do not json.load() the entire OSM chunk here because
    these files can be very large. The actual chunk is loaded only when
    that chunk is processed.
    """

    try:
        if not path.is_file():
            return False

        if path.suffix.lower() != ".json":
            return False

        if not is_osm_chunk_filename(path):
            return False

        if path.stat().st_size <= 0:
            return False

        with path.open("rb") as fh:
            prefix = fh.read(128 * 1024)

        prefix = prefix.lstrip()

        if not prefix.startswith(b"{"):
            return False

        if b'"elements"' not in prefix:
            return False

        return True

    except OSError:
        return False


def find_osm_chunk_cache(
    south: float,
    west: float,
    north: float,
    east: float,
) -> Optional[Path]:

    for candidate in osm_chunk_cache_candidates(
        south,
        west,
        north,
        east,
    ):
        if is_valid_osm_chunk_cache(candidate):
            return candidate

    return None


def save_osm_chunk_cache(
    south: float,
    west: float,
    north: float,
    east: float,
    data: Dict[str, Any],
) -> Path:

    ensure_directories()

    path = osm_chunk_filename(
        south,
        west,
        north,
        east,
    )

    temporary = path.with_suffix(".json.tmp")

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(
            data,
            fh,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    os.replace(temporary, path)

    return path


def load_osm_chunk_cache(path: Path) -> Dict[str, Any]:

    with path.open(
        "r",
        encoding="utf-8",
    ) as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid OSM chunk: {path}")

    if not isinstance(data.get("elements"), list):
        raise ValueError(
            f"OSM chunk has no elements list: {path}"
        )

    return data


# ============================================================================
# OSM chunk generation
# ============================================================================


def generate_osm_chunks(
    south: float,
    west: float,
    north: float,
    east: float,
) -> List[Tuple[float, float, float, float]]:

    south, west, north, east = normalize_bounds(
        south,
        west,
        north,
        east,
    )

    chunks: List[Tuple[float, float, float, float]] = []

    lat = south

    while lat < north - 1e-10:

        chunk_north = min(
            north,
            lat + OSM_CHUNK_SIZE,
        )

        lon = west

        while lon < east - 1e-10:

            chunk_east = min(
                east,
                lon + OSM_CHUNK_SIZE,
            )

            chunk = (
                round(lat, 6),
                round(lon, 6),
                round(chunk_north, 6),
                round(chunk_east, 6),
            )

            chunks.append(chunk)

            lon = round(
                lon + OSM_CHUNK_SIZE,
                6,
            )

        lat = round(
            lat + OSM_CHUNK_SIZE,
            6,
        )

    return chunks


# ============================================================================
# Overpass
# ============================================================================


def build_osm_query(
    south: float,
    west: float,
    north: float,
    east: float,
) -> str:

    bbox = (
        f"{south:.6f},"
        f"{west:.6f},"
        f"{north:.6f},"
        f"{east:.6f}"
    )

    return f"""
[out:json][timeout:180];

(
  way["building"]({bbox});
  way["railway"]({bbox});
  way["highway"]({bbox});
  way["platform"]({bbox});
  way["waterway"]({bbox});
  way["natural"]({bbox});
  way["landuse"]({bbox});
  way["leisure"]({bbox});
  way["power"]({bbox});
  way["bridge"]({bbox});
  way["tunnel"]({bbox});

  node["railway"]({bbox});
  node["place"]({bbox});
  node["amenity"]({bbox});
  node["tourism"]({bbox});
  node["historic"]({bbox});
  node["barrier"]({bbox});
  node["man_made"]({bbox});

  relation["building"]({bbox});
  relation["landuse"]({bbox});
  relation["natural"]({bbox});
  relation["waterway"]({bbox});
);

(._;>;);

out body;
"""


def retry_after_seconds(response: requests.Response) -> float:
    value = response.headers.get("Retry-After")

    if not value:
        return 0.0

    try:
        return min(
            float(value),
            OSM_RETRY_AFTER_MAX,
        )
    except ValueError:
        return 0.0


def request_osm_chunk(
    south: float,
    west: float,
    north: float,
    east: float,
) -> Dict[str, Any]:

    query = build_osm_query(
        south,
        west,
        north,
        east,
    )

    last_error: Optional[Exception] = None

    for server in OVERPASS_SERVERS:

        for attempt in range(OSM_RETRY_ROUNDS):

            try:
                response = requests.post(
                    server,
                    data=query.encode("utf-8"),
                    headers=HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )

                if response.status_code == 200:
                    data = response.json()

                    if not isinstance(data, dict):
                        raise ValueError(
                            "Overpass response is not an object"
                        )

                    return data

                if response.status_code in (
                    429,
                    502,
                    503,
                    504,
                ):

                    retry_after = retry_after_seconds(
                        response
                    )

                    if retry_after <= 0:
                        retry_after = min(
                            OSM_RETRY_MAX,
                            OSM_RETRY_DELAY
                            * (2 ** attempt),
                        )

                    retry_after += random.uniform(
                        0,
                        OSM_RETRY_JITTER,
                    )

                    time.sleep(retry_after)
                    continue

                response.raise_for_status()

            except Exception as exc:
                last_error = exc

                delay = min(
                    OSM_RETRY_MAX,
                    OSM_RETRY_DELAY
                    * (2 ** attempt),
                )

                delay += random.uniform(
                    0,
                    OSM_RETRY_JITTER,
                )

                time.sleep(delay)

        # Move to the next Overpass server.

    if last_error is not None:
        raise RuntimeError(
            f"OSM request failed for "
            f"{south},{west},{north},{east}: "
            f"{last_error}"
        )

    raise RuntimeError(
        f"OSM request failed for "
        f"{south},{west},{north},{east}"
    )


def acquire_osm_chunks(
    chunks: Iterable[Tuple[float, float, float, float]],
) -> Tuple[
    List[Path],
    List[Tuple[float, float, float, float]],
]:

    cached: List[Path] = []
    pending: List[
        Tuple[float, float, float, float]
    ] = []

    chunks = list(chunks)

    print(
        f"[OSM] Checking {len(chunks)} chunks..."
    )

    for index, chunk in enumerate(chunks, 1):

        path = find_osm_chunk_cache(*chunk)

        if path:
            cached.append(path)

        else:
            pending.append(chunk)

        if index % 100 == 0 or index == len(chunks):
            print(
                f"[OSM] Cache scan: "
                f"{index}/{len(chunks)}"
            )

    print(
        f"[OSM] Cached: {len(cached)}"
    )
    print(
        f"[OSM] Missing: {len(pending)}"
    )

    failed: List[
        Tuple[float, float, float, float]
    ] = []

    for index, chunk in enumerate(
        pending,
        1,
    ):

        south, west, north, east = chunk

        print(
            f"[OSM] Downloading "
            f"{index}/{len(pending)}: "
            f"{_osm_chunk_stem(*chunk)}"
        )

        try:
            data = request_osm_chunk(*chunk)

            path = save_osm_chunk_cache(
                *chunk,
                data,
            )

            cached.append(path)

        except Exception as exc:

            print(
                f"[OSM] FAILED "
                f"{_osm_chunk_stem(*chunk)}: "
                f"{exc}"
            )

            failed.append(chunk)

    # Retry failed chunks as a whole.
    for retry_pass in range(
        1,
        OSM_FAILED_CHUNK_RETRY_PASSES + 1,
    ):

        if not failed:
            break

        print(
            f"[OSM] Retry pass "
            f"{retry_pass}/"
            f"{OSM_FAILED_CHUNK_RETRY_PASSES}: "
            f"{len(failed)} chunks"
        )

        remaining = []

        for chunk in failed:

            if find_osm_chunk_cache(*chunk):
                cached.append(
                    osm_chunk_filename(*chunk)
                )
                continue

            try:
                data = request_osm_chunk(*chunk)

                path = save_osm_chunk_cache(
                    *chunk,
                    data,
                )

                cached.append(path)

            except Exception as exc:

                print(
                    f"[OSM] Retry failed "
                    f"{_osm_chunk_stem(*chunk)}: "
                    f"{exc}"
                )

                remaining.append(chunk)

        failed = remaining

    # Deduplicate paths while preserving order.
    cached = list(
        dict.fromkeys(
            cached
        )
    )

    return cached, failed


# ============================================================================
# OSM classification
# ============================================================================


def classify_osm_element(
    element: Dict[str, Any],
) -> Optional[str]:

    tags = normalize_tags(
        element.get("tags")
    )

    if not tags:
        return None

    railway = tags.get("railway")
    building = tags.get("building")
    highway = tags.get("highway")
    natural = tags.get("natural")
    landuse = tags.get("landuse")
    waterway = tags.get("waterway")
    leisure = tags.get("leisure")
    power = tags.get("power")
    platform = tags.get("platform")

    if railway:
        return "railway"

    if building:
        return "building"

    if platform:
        return "platform"

    if highway:
        return "road"

    if waterway:
        return "waterway"

    if natural:
        return "natural"

    if landuse:
        return "landuse"

    if leisure:
        return "leisure"

    if power:
        return "power"

    if tags.get("bridge"):
        return "bridge"

    if tags.get("tunnel"):
        return "tunnel"

    # Tagged nodes can still represent scenery / landmarks.
    if element.get("type") == "node":
        if any(
            key in tags
            for key in (
                "place",
                "amenity",
                "tourism",
                "historic",
                "barrier",
                "man_made",
            )
        ):
            return "point"

    return None


# ============================================================================
# OSM geometry
# ============================================================================


def build_node_index(
    elements: Iterable[Dict[str, Any]],
) -> Dict[int, Tuple[float, float]]:

    nodes: Dict[
        int,
        Tuple[float, float]
    ] = {}

    for element in elements:

        if element.get("type") != "node":
            continue

        node_id = element.get("id")

        if node_id is None:
            continue

        try:
            lat = float(element["lat"])
            lon = float(element["lon"])
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        nodes[int(node_id)] = (
            lat,
            lon,
        )

    return nodes


def geometry_from_osm_element(
    element: Dict[str, Any],
    nodes: Dict[int, Tuple[float, float]],
) -> Optional[Dict[str, Any]]:

    element_type = element.get("type")

    if element_type == "node":

        try:
            lat = float(element["lat"])
            lon = float(element["lon"])
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            return None

        return {
            "type": "Point",
            "coordinates": [
                lon,
                lat,
            ],
        }

    if element_type != "way":
        return None

    refs = element.get("nodes")

    if not isinstance(refs, list):
        return None

    coordinates: List[
        List[float]
    ] = []

    for ref in refs:

        point = nodes.get(
            int(ref)
        )

        if point is None:
            continue

        lat, lon = point

        coordinates.append([
            lon,
            lat,
        ])

    if len(coordinates) < 2:
        return None

    tags = normalize_tags(
        element.get("tags")
    )

    closed = (
        len(coordinates) >= 4
        and coordinates[0] == coordinates[-1]
    )

    polygon_tags = (
        "building",
        "landuse",
        "natural",
        "waterway",
        "leisure",
    )

    if closed and any(
        key in tags
        for key in polygon_tags
    ):

        return {
            "type": "Polygon",
            "coordinates": [
                coordinates
            ],
        }

    return {
        "type": "LineString",
        "coordinates": coordinates,
    }


def geometry_centroid(
    geometry: Dict[str, Any],
) -> Optional[Tuple[float, float]]:

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if not coordinates:
        return None

    points: List[
        Tuple[float, float]
    ] = []

    if geometry_type == "Point":

        try:
            lon, lat = coordinates
            return float(lat), float(lon)
        except (
            TypeError,
            ValueError,
        ):
            return None

    if geometry_type == "LineString":

        source = coordinates

    elif geometry_type == "Polygon":

        if not coordinates:
            return None

        source = coordinates[0]

    else:
        return None

    for point in source:

        if len(point) < 2:
            continue

        try:
            lon = float(point[0])
            lat = float(point[1])
        except (
            TypeError,
            ValueError,
        ):
            continue

        points.append(
            (lat, lon)
        )

    if not points:
        return None

    lat = sum(
        p[0] for p in points
    ) / len(points)

    lon = sum(
        p[1] for p in points
    ) / len(points)

    return lat, lon


# ============================================================================
# 3D metadata
# ============================================================================


def build_3d_metadata(
    tags: Dict[str, str],
    object_type: str,
) -> Dict[str, Any]:

    height = safe_float(
        tags.get("height"),
        0.0,
    )

    levels = safe_int(
        tags.get("building:levels"),
        0,
    )

    if height <= 0 and levels > 0:
        height = levels * 3.0

    if object_type == "building":

        if height <= 0:
            height = 6.0

        if levels <= 0:
            levels = max(
                1,
                int(round(height / 3.0)),
            )

    return {
        "height_m": round(
            height,
            2,
        ),
        "levels": levels,
        "base_elevation_m": 0.0,
    }


def build_osm_object(
    element: Dict[str, Any],
    nodes: Dict[int, Tuple[float, float]],
    source_chunk: str,
) -> Optional[Dict[str, Any]]:

    object_type = classify_osm_element(
        element
    )

    if object_type is None:
        return None

    geometry = geometry_from_osm_element(
        element,
        nodes,
    )

    if geometry is None:
        return None

    tags = normalize_tags(
        element.get("tags")
    )

    centroid = geometry_centroid(
        geometry
    )

    if centroid is None:
        return None

    lat, lon = centroid

    object_id = (
        f"osm:"
        f"{element.get('type', 'unknown')}:"
        f"{element.get('id', '')}"
    )

    metadata_3d = build_3d_metadata(
        tags,
        object_type,
    )

    return {
        "id": object_id,
        "source": "osm",
        "source_chunk": source_chunk,
        "osm_type": element.get("type"),
        "osm_id": element.get("id"),
        "type": object_type,
        "geometry": geometry,
        "latitude": lat,
        "longitude": lon,
        "tags": tags,
        "model": metadata_3d,
    }


# ============================================================================
# SQLite build database
# ============================================================================


class BuildDatabase:

    def __init__(
        self,
        path: Path,
    ) -> None:

        self.connection = sqlite3.connect(
            str(path)
        )

        self.connection.execute(
            "PRAGMA journal_mode=WAL"
        )

        self.connection.execute(
            "PRAGMA synchronous=NORMAL"
        )

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_osm (
                object_key TEXT PRIMARY KEY
            )
            """
        )

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gis_seen (
                object_key TEXT PRIMARY KEY
            )
            """
        )

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS building_cells (
                cell_x INTEGER NOT NULL,
                cell_y INTEGER NOT NULL,
                source TEXT NOT NULL,
                object_id TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL
            )
            """
        )

        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_building_cells
            ON building_cells (
                cell_x,
                cell_y
            )
            """
        )

        self.connection.commit()

    def osm_seen_or_insert(
        self,
        key: str,
    ) -> bool:

        try:
            self.connection.execute(
                "INSERT INTO seen_osm(object_key) VALUES (?)",
                (key,),
            )
            return False

        except sqlite3.IntegrityError:
            return True

    def gis_seen_or_insert(
        self,
        key: str,
    ) -> bool:

        try:
            self.connection.execute(
                "INSERT INTO gis_seen(object_key) VALUES (?)",
                (key,),
            )
            return False

        except sqlite3.IntegrityError:
            return True

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


# ============================================================================
# Staged object store
# ============================================================================


class StagedObjectStore:

    def __init__(
        self,
        path: Path,
    ) -> None:

        self.path = path

        self.file = path.open(
            "a",
            encoding="utf-8",
        )

    def append(
        self,
        obj: Dict[str, Any],
    ) -> None:

        self.file.write(
            json.dumps(
                obj,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        self.file.write("\n")

    def flush(self) -> None:
        self.file.flush()

    def close(self) -> None:
        self.file.flush()
        self.file.close()


def iter_staged_objects(
    path: Path,
) -> Iterator[Dict[str, Any]]:

    with path.open(
        "r",
        encoding="utf-8",
    ) as fh:

        for line in fh:

            line = line.strip()

            if not line:
                continue

            yield json.loads(line)


# ============================================================================
# Spatial helpers
# ============================================================================


def meters_per_degree_lon(
    latitude: float,
) -> float:

    return (
        METERS_PER_DEG_LAT
        * math.cos(
            math.radians(latitude)
        )
    )


def building_cell(
    latitude: float,
    longitude: float,
) -> Tuple[int, int]:

    lat_m = (
        latitude
        * METERS_PER_DEG_LAT
    )

    lon_m = (
        longitude
        * meters_per_degree_lon(latitude)
    )

    return (
        int(
            math.floor(
                lat_m / GRID_CELL_METERS
            )
        ),
        int(
            math.floor(
                lon_m / GRID_CELL_METERS
            )
        ),
    )


def haversine_meters(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:

    radius = 6371000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(
        lat2 - lat1
    )

    dlambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dphi / 2) ** 2
        +
        math.cos(phi1)
        * math.cos(phi2)
        * math.sin(dlambda / 2) ** 2
    )

    return (
        2
        * radius
        * math.asin(
            math.sqrt(a)
        )
    )


def insert_osm_building_centroid(
    database: BuildDatabase,
    object_id: str,
    latitude: float,
    longitude: float,
) -> None:

    cell_x, cell_y = building_cell(
        latitude,
        longitude,
    )

    database.connection.execute(
        """
        INSERT INTO building_cells (
            cell_x,
            cell_y,
            source,
            object_id,
            latitude,
            longitude
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            cell_x,
            cell_y,
            "osm",
            object_id,
            latitude,
            longitude,
        ),
    )


def gis_building_matches_osm(
    database: BuildDatabase,
    latitude: float,
    longitude: float,
) -> bool:

    cell_x, cell_y = building_cell(
        latitude,
        longitude,
    )

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):

            rows = database.connection.execute(
                """
                SELECT
                    latitude,
                    longitude
                FROM building_cells
                WHERE cell_x = ?
                  AND cell_y = ?
                """,
                (
                    cell_x + dx,
                    cell_y + dy,
                ),
            )

            for osm_lat, osm_lon in rows:

                if (
                    haversine_meters(
                        latitude,
                        longitude,
                        osm_lat,
                        osm_lon,
                    )
                    <= BUILDING_RECONCILIATION_DISTANCE_M
                ):
                    return True

    return False


# ============================================================================
# Stage OSM
# ============================================================================


def stage_osm_objects(
    chunk_paths: Iterable[Path],
    database: BuildDatabase,
    store: StagedObjectStore,
) -> int:

    total = 0
    unique = 0

    for chunk_index, path in enumerate(
        chunk_paths,
        1,
    ):

        print(
            f"[SDB] OSM chunk "
            f"{chunk_index}: "
            f"{path.name}"
        )

        data = load_osm_chunk_cache(
            path
        )

        elements = data.get(
            "elements",
            [],
        )

        nodes = build_node_index(
            elements
        )

        for element in elements:

            total += 1

            # Support nodes required for way geometry are not themselves
            # scenery objects unless they carry meaningful tags.
            if (
                element.get("type") == "node"
                and not element.get("tags")
            ):
                continue

            element_id = (
                f"{element.get('type', '')}:"
                f"{element.get('id', '')}"
            )

            if database.osm_seen_or_insert(
                element_id
            ):
                continue

            obj = build_osm_object(
                element,
                nodes,
                path.name,
            )

            if obj is None:
                continue

            store.append(obj)
            unique += 1

            if (
                unique % PRINT_EVERY_OSM_OBJECTS
                == 0
            ):
                print(
                    f"[SDB] OSM objects staged: "
                    f"{unique}"
                )

            if obj["type"] == "building":

                insert_osm_building_centroid(
                    database,
                    obj["id"],
                    obj["latitude"],
                    obj["longitude"],
                )

        database.commit()

        # Important: only one OSM chunk should remain alive at a time.
        del data
        del elements
        del nodes

        gc.collect()

    store.flush()

    print(
        f"[SDB] OSM elements inspected: {total}"
    )
    print(
        f"[SDB] OSM objects staged: {unique}"
    )

    return unique


# ============================================================================
# GIS dataset links
# ============================================================================


def load_gis_dataset_links() -> List[Dict[str, str]]:

    response = requests.get(
        GIS_DATASET_LINKS_URL,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    text = response.text

    reader = csv.DictReader(
        text.splitlines()
    )

    rows = []

    for row in reader:

        normalized = {
            str(k).strip(): str(v).strip()
            for k, v in row.items()
            if k is not None and v is not None
        }

        rows.append(normalized)

    return rows


def extract_partition_id(
    row: Dict[str, str],
) -> Optional[str]:

    for key, value in row.items():

        key_lower = key.lower()

        if (
            "partition" in key_lower
            or key_lower.endswith("id")
        ):

            value = value.strip()

            if re.fullmatch(
                r"[0-9a-fA-F]{16}",
                value,
            ):
                return value

    for value in row.values():

        value = value.strip()

        if re.fullmatch(
            r"[0-9a-fA-F]{16}",
            value,
        ):
            return value

    return None


def extract_partition_url(
    row: Dict[str, str],
) -> Optional[str]:

    for value in row.values():

        value = value.strip()

        if value.startswith(
            "http://"
        ) or value.startswith(
            "https://"
        ):
            return value

    return None


# ============================================================================
# GIS filename format
# ============================================================================


def gis_partition_filename(
    partition_id: str,
) -> Path:

    """
    GIS cache format is ALWAYS:

        <partition-id>.jsonl.gz

    Example:

        0a3b9b39bfd1b9f6.jsonl.gz
    """

    partition_id = partition_id.strip()

    return (
        GIS_PARTITION_DIRECTORY
        / f"{partition_id}.jsonl.gz"
    )


def is_valid_gis_partition_cache(
    path: Path,
) -> bool:

    if not path.is_file():
        return False

    if not path.name.endswith(
        ".jsonl.gz"
    ):
        return False

    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def download_gis_partition(
    partition_id: str,
    url: str,
) -> Path:

    ensure_directories()

    destination = gis_partition_filename(
        partition_id
    )

    temporary = destination.with_suffix(
        ".jsonl.gz.tmp"
    )

    print(
        f"[GIS] Downloading "
        f"{destination.name}"
    )

    with requests.get(
        url,
        headers=HEADERS,
        timeout=GIS_DOWNLOAD_TIMEOUT,
        stream=True,
    ) as response:

        response.raise_for_status()

        with temporary.open(
            "wb"
        ) as fh:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    fh.write(chunk)

    os.replace(
        temporary,
        destination,
    )

    return destination


# ============================================================================
# GIS partition parsing
# ============================================================================


def iter_gis_partition(
    path: Path,
) -> Iterator[Dict[str, Any]]:

    """
    GIS files are JSON Lines compressed with gzip:

        0a3b9b39bfd1b9f6.jsonl.gz

    One JSON object per line.
    """

    with gzip.open(
        path,
        "rt",
        encoding="utf-8",
    ) as fh:

        for line_number, line in enumerate(
            fh,
            1,
        ):

            line = line.strip()

            if not line:
                continue

            try:
                value = json.loads(line)

            except json.JSONDecodeError as exc:

                print(
                    f"[GIS] Invalid JSON at "
                    f"{path.name}:{line_number}: "
                    f"{exc}"
                )

                continue

            if isinstance(value, dict):
                yield value


# ============================================================================
# GIS geometry
# ============================================================================


def convert_gis_geometry(
    geometry: Any,
) -> Optional[Dict[str, Any]]:

    if not isinstance(
        geometry,
        dict,
    ):
        return None

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if not geometry_type or coordinates is None:
        return None

    return {
        "type": geometry_type,
        "coordinates": coordinates,
    }


def convert_gis_building(
    feature: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    geometry = convert_gis_geometry(
        feature.get("geometry")
    )

    if geometry is None:
        return None

    properties = feature.get(
        "properties"
    )

    if not isinstance(
        properties,
        dict,
    ):
        properties = {}

    # GIS datasets can vary in their identifier field.
    source_id = None

    for key in (
        "id",
        "ID",
        "fid",
        "FID",
        "objectid",
        "OBJECTID",
    ):

        if key in properties:
            source_id = properties[key]
            break

    if source_id is None:
        source_id = feature.get(
            "id"
        )

    if source_id is None:
        # Stable hash of the feature.
        source_id = hashlib.sha1(
            json.dumps(
                feature,
                sort_keys=True,
                ensure_ascii=False,
            ).encode(
                "utf-8"
            )
        ).hexdigest()

    centroid = geometry_centroid(
        geometry
    )

    if centroid is None:
        return None

    latitude, longitude = centroid

    tags = {
        str(k): str(v)
        for k, v in properties.items()
        if v is not None
    }

    return {
        "id": f"gis:building:{source_id}",
        "source": "gis",
        "type": "building",
        "geometry": geometry,
        "latitude": latitude,
        "longitude": longitude,
        "tags": tags,
        "model": build_3d_metadata(
            tags,
            "building",
        ),
    }


# ============================================================================
# Stage GIS
# ============================================================================


def stage_gis_buildings(
    partition_paths: Iterable[Path],
    database: BuildDatabase,
    store: StagedObjectStore,
) -> int:

    total = 0
    unique = 0
    skipped_duplicate = 0
    skipped_reconciled = 0

    for partition_index, path in enumerate(
        partition_paths,
        1,
    ):

        print(
            f"[SDB] GIS partition "
            f"{partition_index}: "
            f"{path.name}"
        )

        for feature in iter_gis_partition(
            path
        ):

            total += 1

            tags = normalize_tags(
                feature.get(
                    "properties"
                )
            )

            # We are interested in buildings.
            building_value = (
                tags.get("building")
                or tags.get("building_type")
                or tags.get("type")
            )

            if (
                building_value is None
                and "building" not in tags
            ):
                continue

            obj = convert_gis_building(
                feature
            )

            if obj is None:
                continue

            key = obj["id"]

            if database.gis_seen_or_insert(
                key
            ):
                skipped_duplicate += 1
                continue

            # If the GIS building is already represented by a nearby
            # OSM building, don't duplicate it.
            if gis_building_matches_osm(
                database,
                obj["latitude"],
                obj["longitude"],
            ):
                skipped_reconciled += 1
                continue

            store.append(obj)
            unique += 1

            if (
                unique
                % PRINT_EVERY_GIS_BUILDINGS
                == 0
            ):
                print(
                    f"[SDB] GIS buildings staged: "
                    f"{unique}"
                )

        database.commit()

    store.flush()

    print(
        f"[SDB] GIS features inspected: {total}"
    )
    print(
        f"[SDB] GIS buildings staged: {unique}"
    )
    print(
        f"[SDB] GIS duplicates skipped: "
        f"{skipped_duplicate}"
    )
    print(
        f"[SDB] GIS/OSM reconciled: "
        f"{skipped_reconciled}"
    )

    return unique


# ============================================================================
# Compatibility helpers
# ============================================================================


def query_osm_scenery(
    south: float,
    west: float,
    north: float,
    east: float,
) -> Dict[str, Any]:

    """
    Compatibility function.

    The actual OSM scenery is stored in chunk files.
    This function returns chunk metadata rather than keeping a
    massive OSM response in memory.
    """

    chunks = generate_osm_chunks(
        south,
        west,
        north,
        east,
    )

    return {
        "version": SDB_VERSION,
        "bounds": {
            "south": south,
            "west": west,
            "north": north,
            "east": east,
        },
        "chunks": [
            {
                "south": s,
                "west": w,
                "north": n,
                "east": e,
                "file": osm_chunk_filename(
                    s,
                    w,
                    n,
                    e,
                ).name,
            }
            for s, w, n, e in chunks
        ],
        "elements": [],
    }


def finalize_object_3d_metadata(
    obj: Dict[str, Any],
) -> Dict[str, Any]:

    if "model" not in obj:
        obj["model"] = build_3d_metadata(
            normalize_tags(
                obj.get("tags")
            ),
            str(
                obj.get(
                    "type",
                    "object",
                )
            ),
        )

    return obj


def finalize_objects(
    objects: Iterable[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:

    for obj in objects:
        yield finalize_object_3d_metadata(
            obj
        )


def calculate_statistics(
    objects_path: Path,
) -> Dict[str, Any]:

    total = 0
    by_source: Dict[
        str,
        int
    ] = {}

    by_type: Dict[
        str,
        int
    ] = {}

    for obj in iter_staged_objects(
        objects_path
    ):

        total += 1

        source = str(
            obj.get(
                "source",
                "unknown",
            )
        )

        object_type = str(
            obj.get(
                "type",
                "unknown",
            )
        )

        by_source[source] = (
            by_source.get(
                source,
                0,
            )
            + 1
        )

        by_type[object_type] = (
            by_type.get(
                object_type,
                0,
            )
            + 1
        )

    return {
        "total_objects": total,
        "by_source": by_source,
        "by_type": by_type,
    }


# ============================================================================
# Stage management
# ============================================================================


def reset_stage() -> None:

    for path in (
        SDB_STAGE_OBJECTS,
        SDB_STAGE_DATABASE,
        SDB_STAGE_DATABASE.with_suffix(
            ".sqlite-wal"
        ),
        SDB_STAGE_DATABASE.with_suffix(
            ".sqlite-shm"
        ),
    ):

        try:
            path.unlink()

        except FileNotFoundError:
            pass

        except OSError as exc:
            print(
                f"[SDB] Could not remove "
                f"{path}: {exc}"
            )


# ============================================================================
# GIS partition acquisition
# ============================================================================


def acquire_gis_partitions() -> List[Path]:

    ensure_directories()

    try:
        rows = load_gis_dataset_links()

    except Exception as exc:

        print(
            f"[GIS] Could not load dataset links: "
            f"{exc}"
        )

        return []

    partitions: List[Path] = []

    for row in rows:

        partition_id = extract_partition_id(
            row
        )

        url = extract_partition_url(
            row
        )

        if not partition_id or not url:
            continue

        cached = gis_partition_filename(
            partition_id
        )

        if is_valid_gis_partition_cache(
            cached
        ):
            partitions.append(cached)
            continue

        try:

            downloaded = download_gis_partition(
                partition_id,
                url,
            )

            partitions.append(
                downloaded
            )

        except Exception as exc:

            print(
                f"[GIS] Failed partition "
                f"{partition_id}: "
                f"{exc}"
            )

    return list(
        dict.fromkeys(
            partitions
        )
    )


# ============================================================================
# Main build
# ============================================================================


def build_sdb(
    south: float,
    west: float,
    north: float,
    east: float,
    route_name: str = "TrainGo Route",
    padding: float = 0.0,
    output: Optional[Path] = None,
) -> Dict[str, Any]:

    ensure_directories()

    south, west, north, east = expand_bounds(
        south,
        west,
        north,
        east,
        padding,
    )

    south, west, north, east = normalize_bounds(
        south,
        west,
        north,
        east,
    )

    print()
    print("=" * 72)
    print("TrainGo SDB Builder")
    print("=" * 72)
    print(
        f"Route : {route_name}"
    )
    print(
        f"Bounds: "
        f"{south:.6f}, "
        f"{west:.6f}, "
        f"{north:.6f}, "
        f"{east:.6f}"
    )
    print("=" * 72)
    print()

    # ------------------------------------------------------------------
    # OSM
    # ------------------------------------------------------------------

    osm_chunks = generate_osm_chunks(
        south,
        west,
        north,
        east,
    )

    print(
        f"[OSM] Required chunks: "
        f"{len(osm_chunks)}"
    )

    osm_paths, failed_osm = acquire_osm_chunks(
        osm_chunks
    )

    if failed_osm:

        print()
        print(
            "[WARNING] Some OSM chunks could not "
            "be acquired:"
        )

        for chunk in failed_osm:
            print(
                "  ",
                _osm_chunk_stem(*chunk),
            )

        print()

    # ------------------------------------------------------------------
    # Reset temporary stage
    # ------------------------------------------------------------------

    reset_stage()

    database = BuildDatabase(
        SDB_STAGE_DATABASE
    )

    store = StagedObjectStore(
        SDB_STAGE_OBJECTS
    )

    try:

        # --------------------------------------------------------------
        # Stage OSM
        # --------------------------------------------------------------

        osm_count = stage_osm_objects(
            osm_paths,
            database,
            store,
        )

        # --------------------------------------------------------------
        # GIS
        # --------------------------------------------------------------

        gis_paths = acquire_gis_partitions()

        gis_count = stage_gis_buildings(
            gis_paths,
            database,
            store,
        )

        store.close()
        database.close()

    except Exception:

        store.close()

        try:
            database.close()
        except Exception:
            pass

        raise

    statistics = calculate_statistics(
        SDB_STAGE_OBJECTS
    )

    metadata = {
        "version": SDB_VERSION,
        "route_name": route_name,
        "bounds": {
            "south": south,
            "west": west,
            "north": north,
            "east": east,
        },
        "osm": {
            "chunk_size": OSM_CHUNK_SIZE,
            "requested_chunks": len(
                osm_chunks
            ),
            "cached_or_downloaded_chunks": len(
                osm_paths
            ),
            "failed_chunks": len(
                failed_osm
            ),
            "objects": osm_count,
        },
        "gis": {
            "partitions": len(
                gis_paths
            ),
            "objects": gis_count,
        },
        "statistics": statistics,
    }

    destination = (
        output
        if output is not None
        else DEFAULT_SDB_FILE
    )

    write_sdb(
        destination,
        metadata,
        SDB_STAGE_OBJECTS,
    )

    return metadata


# ============================================================================
# SDB serialization
# ============================================================================


def serialize_value(
    value: Any,
) -> Any:

    if isinstance(
        value,
        Path,
    ):
        return str(value)

    if isinstance(
        value,
        dict,
    ):
        return {
            str(k): serialize_value(v)
            for k, v in value.items()
        }

    if isinstance(
        value,
        list,
    ):
        return [
            serialize_value(v)
            for v in value
        ]

    if isinstance(
        value,
        tuple,
    ):
        return [
            serialize_value(v)
            for v in value
        ]

    return value


def _write_json(
    fh,
    value: Any,
) -> None:

    fh.write(
        json.dumps(
            serialize_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    fh.write("\n")


def serialize_sdb(
    output: Path,
    metadata: Dict[str, Any],
    staged_objects: Path,
) -> None:

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = output.with_suffix(
        output.suffix + ".tmp"
    )

    header = {
        "format": "TrainGo-SDB",
        "version": SDB_VERSION,
        "metadata": metadata,
    }

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as fh:

        _write_json(
            fh,
            header,
        )

        for obj in finalize_objects(
            iter_staged_objects(
                staged_objects
            )
        ):

            _write_json(
                fh,
                obj,
            )

    os.replace(
        temporary,
        output,
    )


def write_sdb(
    output: Path,
    metadata: Dict[str, Any],
    staged_objects: Path,
) -> None:

    print()
    print(
        f"[SDB] Writing: {output}"
    )

    serialize_sdb(
        output,
        metadata,
        staged_objects,
    )

    try:
        size_mb = (
            output.stat().st_size
            / (1024 * 1024)
        )

        print(
            f"[SDB] Size: "
            f"{size_mb:.2f} MB"
        )

    except OSError:
        pass


def export_sdb(
    output: Path = DEFAULT_SDB_FILE,
) -> Path:

    if not SDB_STAGE_OBJECTS.exists():
        raise FileNotFoundError(
            "No staged scenery objects found."
        )

    statistics = calculate_statistics(
        SDB_STAGE_OBJECTS
    )

    metadata = {
        "version": SDB_VERSION,
        "statistics": statistics,
    }

    write_sdb(
        output,
        metadata,
        SDB_STAGE_OBJECTS,
    )

    return output


# ============================================================================
# Cache status
# ============================================================================


def get_cached_osm_chunks() -> List[Path]:

    ensure_directories()

    paths = []

    for path in OSM_CHUNK_DIRECTORY.iterdir():

        if not path.is_file():
            continue

        # Only the exact OSM format is accepted.
        if not is_osm_chunk_filename(path):
            continue

        if is_valid_osm_chunk_cache(path):
            paths.append(path)

    return sorted(
        paths,
        key=lambda p: p.name,
    )


def get_cached_gis_partitions() -> List[Path]:

    ensure_directories()

    paths = []

    for path in GIS_PARTITION_DIRECTORY.iterdir():

        if not path.is_file():
            continue

        # Only:
        #   <partition>.jsonl.gz
        #
        # is accepted.
        if not path.name.endswith(
            ".jsonl.gz"
        ):
            continue

        if is_valid_gis_partition_cache(
            path
        ):
            paths.append(path)

    return sorted(
        paths,
        key=lambda p: p.name,
    )


def scenery_cache_status() -> Dict[str, Any]:

    osm = get_cached_osm_chunks()
    gis = get_cached_gis_partitions()

    print()
    print("=" * 72)
    print("TrainGo Scenery Cache")
    print("=" * 72)

    print()
    print(
        f"OSM chunks: "
        f"{len(osm)}"
    )

    for path in osm:
        print(
            f"  {path.name}"
        )

    print()
    print(
        f"GIS partitions: "
        f"{len(gis)}"
    )

    for path in gis:
        print(
            f"  {path.name}"
        )

    print()
    print("=" * 72)

    return {
        "osm_chunks": [
            str(p)
            for p in osm
        ],
        "gis_partitions": [
            str(p)
            for p in gis
        ],
    }


# ============================================================================
# CLI
# ============================================================================


def build_argument_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "TrainGo scenery database generator"
        )
    )

    # Optional so --cache-status can be used
    # without supplying geographical bounds.
    parser.add_argument(
        "south",
        nargs="?",
        type=float,
    )

    parser.add_argument(
        "west",
        nargs="?",
        type=float,
    )

    parser.add_argument(
        "north",
        nargs="?",
        type=float,
    )

    parser.add_argument(
        "east",
        nargs="?",
        type=float,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SDB_FILE,
    )

    parser.add_argument(
        "--route-name",
        default="TrainGo Route",
    )

    parser.add_argument(
        "--padding",
        type=float,
        default=0.0,
        help="Padding in degrees",
    )

    parser.add_argument(
        "--cache-status",
        action="store_true",
        help="Show cached OSM/GIS scenery",
    )

    parser.add_argument(
        "--export-stage",
        action="store_true",
        help="Export the current staged scenery",
    )

    return parser


def main() -> int:

    parser = build_argument_parser()

    args = parser.parse_args()

    ensure_directories()

    if args.cache_status:

        scenery_cache_status()

        return 0

    if args.export_stage:

        export_sdb(
            args.output
        )

        return 0

    missing = []

    if args.south is None:
        missing.append("south")

    if args.west is None:
        missing.append("west")

    if args.north is None:
        missing.append("north")

    if args.east is None:
        missing.append("east")

    if missing:

        parser.error(
            "Missing bounds: "
            + ", ".join(missing)
        )

    build_sdb(
        args.south,
        args.west,
        args.north,
        args.east,
        route_name=args.route_name,
        padding=args.padding,
        output=args.output,
    )

    print()
    print(
        "SDB BUILD COMPLETE."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )