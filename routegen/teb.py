import gzip
import json
import math
import os
from pathlib import Path

import numpy as np
import requests


# ============================================================
# TrainGo Terrain Database
# ============================================================

TEB_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent.parent

TEB_DIRECTORY = ROOT / "output" / "terrain"
DEM_DIRECTORY = TEB_DIRECTORY / "dem"
SATELLITE_DIRECTORY = TEB_DIRECTORY / "satellite"

DEFAULT_TEB_FILE = ROOT / "output" / "terrain.teb"


# ============================================================
# Configuration
# ============================================================

# DEM source:
# AWS Terrain Tiles / SRTM-derived HGT data
DEM_BASE_URL = (
    "https://elevation-tiles-prod.s3.amazonaws.com/"
    "skadi"
)

# Satellite source:
# Esri World Imagery tile service
SATELLITE_TILE_URL = (
    "https://server.arcgisonline.com/"
    "ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/"
    "{z}/{y}/{x}"
)

SATELLITE_ZOOM = 15

REQUEST_TIMEOUT = 60

HEADERS = {
    "User-Agent": "TrainGo-RouteGen/0.1"
}


# ============================================================
# Geographic utilities
# ============================================================

def expand_bounds(
    south,
    west,
    north,
    east,
    margin
):
    return (
        south - margin,
        west - margin,
        north + margin,
        east + margin
    )


def latlon_to_tile(
    latitude,
    longitude,
    zoom
):
    latitude = max(
        min(latitude, 85.05112878),
        -85.05112878
    )

    n = 2 ** zoom

    x = int(
        (longitude + 180.0)
        / 360.0
        * n
    )

    latitude_rad = math.radians(
        latitude
    )

    y = int(
        (
            1.0
            - math.asinh(
                math.tan(latitude_rad)
            ) / math.pi
        )
        / 2.0
        * n
    )

    x = max(
        0,
        min(x, n - 1)
    )

    y = max(
        0,
        min(y, n - 1)
    )

    return x, y


def tile_to_latlon(
    x,
    y,
    zoom
):
    n = 2 ** zoom

    longitude = (
        x / n * 360.0
        - 180.0
    )

    latitude = math.degrees(
        math.atan(
            math.sinh(
                math.pi
                * (
                    1
                    - 2 * y / n
                )
            )
        )
    )

    return latitude, longitude


def calculate_tile_range(
    south,
    west,
    north,
    east,
    zoom
):
    x_min, y_max = latlon_to_tile(
        south,
        west,
        zoom
    )

    x_max, y_min = latlon_to_tile(
        north,
        east,
        zoom
    )

    return (
        x_min,
        x_max,
        y_min,
        y_max
    )


# ============================================================
# Terrain cache
# ============================================================

def terrain_cache_exists():
    """
    Return True when the terrain directory exists and contains
    at least one cached DEM or satellite file.
    """

    if not TEB_DIRECTORY.exists():
        return False

    dem_exists = (
        DEM_DIRECTORY.exists()
        and any(
            DEM_DIRECTORY.rglob("*.hgt")
        )
    )

    satellite_exists = (
        SATELLITE_DIRECTORY.exists()
        and any(
            SATELLITE_DIRECTORY.rglob("*.jpg")
        )
    )

    return dem_exists or satellite_exists


def print_cache_status():
    """
    Print the current terrain cache state.
    """

    print()
    print("Terrain cache")
    print("-------------")

    if not TEB_DIRECTORY.exists():
        print("No terrain cache found.")
        print("Terrain acquisition required.")
        return

    dem_count = 0
    satellite_count = 0

    if DEM_DIRECTORY.exists():
        dem_count = sum(
            1
            for path in DEM_DIRECTORY.rglob("*.hgt")
            if path.is_file()
        )

    if SATELLITE_DIRECTORY.exists():
        satellite_count = sum(
            1
            for path in SATELLITE_DIRECTORY.rglob("*.jpg")
            if path.is_file()
        )

    if dem_count == 0 and satellite_count == 0:
        print("Terrain directory exists but contains no usable data.")
        print("Terrain acquisition required.")
        return

    print("✓ Existing terrain data found.")

    print(
        f"  Cached DEM tiles:       "
        f"{dem_count}"
    )

    print(
        f"  Cached satellite tiles: "
        f"{satellite_count}"
    )

    print("  Cached terrain will be reused.")


# ============================================================
# DEM tile handling
# ============================================================

def latitude_band(latitude):
    if latitude >= 0:
        return f"N{int(latitude):02d}"

    return f"S{abs(int(latitude)):02d}"


def longitude_band(longitude):
    if longitude >= 0:
        return f"E{int(longitude):03d}"

    return f"W{abs(int(longitude)):03d}"


def dem_tile_name(
    latitude,
    longitude
):
    return (
        f"{latitude_band(latitude)}"
        f"{longitude_band(longitude)}"
    )


def download_dem_tile(
    latitude,
    longitude
):
    name = dem_tile_name(
        latitude,
        longitude
    )

    band = latitude_band(latitude)

    url = (
        f"{DEM_BASE_URL}/"
        f"{band}/"
        f"{name}.hgt.gz"
    )

    output_file = (
        DEM_DIRECTORY
        / f"{name}.hgt"
    )

    compressed_file = (
        DEM_DIRECTORY
        / f"{name}.hgt.gz"
    )

    DEM_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True
    )

    if output_file.exists():
        print(
            f"  DEM cached: {name}"
        )

        return output_file

    print(
        f"  Downloading DEM: {name}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    with open(
        compressed_file,
        "wb"
    ) as file:
        file.write(
            response.content
        )

    with gzip.open(
        compressed_file,
        "rb"
    ) as source:
        with open(
            output_file,
            "wb"
        ) as destination:
            destination.write(
                source.read()
            )

    compressed_file.unlink(
        missing_ok=True
    )

    return output_file


def read_hgt(
    hgt_file
):
    file_size = (
        hgt_file.stat().st_size
    )

    samples = int(
        math.sqrt(
            file_size / 2
        )
    )

    if samples * samples * 2 != file_size:
        raise ValueError(
            f"Invalid HGT file: {hgt_file}"
        )

    data = np.fromfile(
        hgt_file,
        dtype=">i2"
    )

    data = data.reshape(
        (samples, samples)
    )

    return data


def collect_dem_tiles(
    south,
    west,
    north,
    east
):
    min_lat = math.floor(south)
    max_lat = math.floor(north)

    min_lon = math.floor(west)
    max_lon = math.floor(east)

    tiles = []

    print()
    print("DEM acquisition")
    print("----------------")

    total = (
        max_lat - min_lat + 1
    ) * (
        max_lon - min_lon + 1
    )

    count = 0
    cached_count = 0
    downloaded_count = 0

    for latitude in range(
        min_lat,
        max_lat + 1
    ):
        for longitude in range(
            min_lon,
            max_lon + 1
        ):
            count += 1

            name = dem_tile_name(
                latitude,
                longitude
            )

            expected_file = (
                DEM_DIRECTORY
                / f"{name}.hgt"
            )

            if expected_file.exists():
                cached_count += 1

                print(
                    f"  [{count}/{total}] "
                    f"DEM cached: {name}"
                )

                tile = expected_file

            else:
                downloaded_count += 1

                print(
                    f"  [{count}/{total}] "
                    f"DEM required: {name}"
                )

                tile = download_dem_tile(
                    latitude,
                    longitude
                )

            tiles.append({
                "name": name,
                "latitude": latitude,
                "longitude": longitude,
                "file": str(
                    tile.relative_to(ROOT)
                ),
                "samples": int(
                    math.sqrt(
                        tile.stat().st_size / 2
                    )
                )
            })

    print()
    print(
        f"DEM cached:      {cached_count}"
    )

    print(
        f"DEM downloaded:  {downloaded_count}"
    )

    return tiles


# ============================================================
# DEM statistics
# ============================================================

def calculate_dem_statistics(
    dem_tiles
):
    minimum = None
    maximum = None
    valid_samples = 0

    for tile in dem_tiles:
        path = ROOT / tile["file"]

        data = read_hgt(path)

        valid = data[
            data != -32768
        ]

        if valid.size == 0:
            continue

        tile_min = int(
            valid.min()
        )

        tile_max = int(
            valid.max()
        )

        if minimum is None:
            minimum = tile_min
        else:
            minimum = min(
                minimum,
                tile_min
            )

        if maximum is None:
            maximum = tile_max
        else:
            maximum = max(
                maximum,
                tile_max
            )

        valid_samples += int(
            valid.size
        )

    return {
        "minimum_elevation_m": minimum,
        "maximum_elevation_m": maximum,
        "valid_samples": valid_samples
    }


# ============================================================
# Satellite imagery
# ============================================================

def satellite_tile_path(
    x,
    y,
    zoom
):
    return (
        SATELLITE_DIRECTORY
        / str(zoom)
        / str(x)
        / f"{y}.jpg"
    )


def download_satellite_tile(
    x,
    y,
    zoom
):
    SATELLITE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = satellite_tile_path(
        x,
        y,
        zoom
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if output_file.exists():
        return output_file

    url = SATELLITE_TILE_URL.format(
        z=zoom,
        x=x,
        y=y
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    with open(
        output_file,
        "wb"
    ) as file:
        file.write(
            response.content
        )

    return output_file


def collect_satellite_tiles(
    south,
    west,
    north,
    east,
    zoom=SATELLITE_ZOOM
):
    (
        x_min,
        x_max,
        y_min,
        y_max
    ) = calculate_tile_range(
        south,
        west,
        north,
        east,
        zoom
    )

    tiles = []

    print()
    print("Satellite acquisition")
    print("---------------------")

    total = (
        x_max - x_min + 1
    ) * (
        y_max - y_min + 1
    )

    print(
        f"Zoom: {zoom}"
    )

    print(
        f"Tile range: "
        f"X {x_min}-{x_max}, "
        f"Y {y_min}-{y_max}"
    )

    print(
        f"Required satellite tiles: "
        f"{total}"
    )

    count = 0
    cached_count = 0
    downloaded_count = 0

    for x in range(
        x_min,
        x_max + 1
    ):
        for y in range(
            y_min,
            y_max + 1
        ):
            count += 1

            path = satellite_tile_path(
                x,
                y,
                zoom
            )

            if path.exists():
                cached_count += 1

                print(
                    f"  [{count}/{total}] "
                    f"Satellite cached "
                    f"{zoom}/{x}/{y}"
                )

            else:
                downloaded_count += 1

                print(
                    f"  [{count}/{total}] "
                    f"Satellite downloading "
                    f"{zoom}/{x}/{y}"
                )

                path = download_satellite_tile(
                    x,
                    y,
                    zoom
                )

            tiles.append({
                "zoom": zoom,
                "x": x,
                "y": y,
                "file": str(
                    path.relative_to(ROOT)
                )
            })

    print()
    print(
        f"Satellite cached:      "
        f"{cached_count}"
    )

    print(
        f"Satellite downloaded:  "
        f"{downloaded_count}"
    )

    return tiles


# ============================================================
# TEB builder
# ============================================================

def build_teb(
    south,
    west,
    north,
    east,
    margin=0.02,
    satellite_zoom=SATELLITE_ZOOM
):
    (
        coverage_south,
        coverage_west,
        coverage_north,
        coverage_east
    ) = expand_bounds(
        south,
        west,
        north,
        east,
        margin
    )

    print()
    print("================================")
    print("      TrainGo TEB Builder")
    print("================================")
    print()

    print("Terrain coverage")
    print("-----------------")

    print(
        f"South: {coverage_south}"
    )

    print(
        f"West:  {coverage_west}"
    )

    print(
        f"North: {coverage_north}"
    )

    print(
        f"East:  {coverage_east}"
    )

    print_cache_status()

    # --------------------------------------------------------
    # DEM
    # --------------------------------------------------------

    dem_tiles = collect_dem_tiles(
        coverage_south,
        coverage_west,
        coverage_north,
        coverage_east
    )

    dem_statistics = (
        calculate_dem_statistics(
            dem_tiles
        )
    )

    # --------------------------------------------------------
    # Satellite
    # --------------------------------------------------------

    satellite_tiles = (
        collect_satellite_tiles(
            coverage_south,
            coverage_west,
            coverage_north,
            coverage_east,
            satellite_zoom
        )
    )

    teb = {
        "database_type": "TEB",
        "database_version": TEB_VERSION,

        "coverage": {
            "south": coverage_south,
            "west": coverage_west,
            "north": coverage_north,
            "east": coverage_east
        },

        "dem": {
            "source": "AWS Terrain Tiles / SRTM",
            "format": "HGT",
            "tiles": dem_tiles,
            "statistics": dem_statistics
        },

        "satellite": {
            "source": "Esri World Imagery",
            "format": "JPEG tiles",
            "zoom": satellite_zoom,
            "tiles": satellite_tiles
        }
    }

    return teb


# ============================================================
# TEB serializer
# ============================================================

def _format_scalar(value):
    if value is None:
        return "NULL"

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, str):
        text = (
            value
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )
        return f'"{text}"'

    return str(value)


def _write_indent(
    file,
    level
):
    file.write(
        "    " * level
    )


def _write_value(
    file,
    key,
    value,
    level
):
    _write_indent(
        file,
        level
    )

    file.write(
        f"{key} = "
        f"{_format_scalar(value)}\n"
    )


def serialize_teb(
    teb,
    output_path=DEFAULT_TEB_FILE
):
    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = output_path.with_suffix(
        output_path.suffix + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            "TRAIN_GO_TERRAIN_DATABASE\n"
        )

        file.write("{\n")

        _write_value(
            file,
            "DATABASE_TYPE",
            teb["database_type"],
            1
        )

        _write_value(
            file,
            "DATABASE_VERSION",
            teb["database_version"],
            1
        )

        # ----------------------------------------------------
        # Coverage
        # ----------------------------------------------------

        _write_indent(
            file,
            1
        )

        file.write(
            "COVERAGE\n"
        )

        _write_indent(
            file,
            1
        )

        file.write("{\n")

        for key, value in (
            teb["coverage"].items()
        ):
            _write_value(
                file,
                key.upper(),
                value,
                2
            )

        _write_indent(
            file,
            1
        )

        file.write(
            "}\n"
        )

        # ----------------------------------------------------
        # DEM
        # ----------------------------------------------------

        _write_indent(
            file,
            1
        )

        file.write(
            "DEM\n"
        )

        _write_indent(
            file,
            1
        )

        file.write("{\n")

        _write_value(
            file,
            "SOURCE",
            teb["dem"]["source"],
            2
        )

        _write_value(
            file,
            "FORMAT",
            teb["dem"]["format"],
            2
        )

        statistics = (
            teb["dem"]["statistics"]
        )

        _write_indent(
            file,
            2
        )

        file.write(
            "STATISTICS\n"
        )

        _write_indent(
            file,
            2
        )

        file.write("{\n")

        for key, value in (
            statistics.items()
        ):
            _write_value(
                file,
                key.upper(),
                value,
                3
            )

        _write_indent(
            file,
            2
        )

        file.write(
            "}\n"
        )

        _write_indent(
            file,
            2
        )

        file.write(
            "TILES\n"
        )

        _write_indent(
            file,
            2
        )

        file.write(
            "{\n"
        )

        for index, tile in enumerate(
            teb["dem"]["tiles"],
            start=1
        ):
            _write_indent(
                file,
                3
            )

            file.write(
                f"TILE {index}\n"
            )

            _write_indent(
                file,
                3
            )

            file.write(
                "{\n"
            )

            _write_value(
                file,
                "NAME",
                tile["name"],
                4
            )

            _write_value(
                file,
                "LATITUDE",
                tile["latitude"],
                4
            )

            _write_value(
                file,
                "LONGITUDE",
                tile["longitude"],
                4
            )

            _write_value(
                file,
                "SAMPLES",
                tile["samples"],
                4
            )

            _write_value(
                file,
                "FILE",
                tile["file"],
                4
            )

            _write_indent(
                file,
                3
            )

            file.write(
                "}\n"
            )

        _write_indent(
            file,
            2
        )

        file.write(
            "}\n"
        )

        _write_indent(
            file,
            1
        )

        file.write(
            "}\n"
        )

        # ----------------------------------------------------
        # Satellite
        # ----------------------------------------------------

        _write_indent(
            file,
            1
        )

        file.write(
            "SATELLITE\n"
        )

        _write_indent(
            file,
            1
        )

        file.write(
            "{\n"
        )

        _write_value(
            file,
            "SOURCE",
            teb["satellite"]["source"],
            2
        )

        _write_value(
            file,
            "FORMAT",
            teb["satellite"]["format"],
            2
        )

        _write_value(
            file,
            "ZOOM",
            teb["satellite"]["zoom"],
            2
        )

        _write_indent(
            file,
            2
        )

        file.write(
            "TILES\n"
        )

        _write_indent(
            file,
            2
        )

        file.write(
            "{\n"
        )

        for index, tile in enumerate(
            teb["satellite"]["tiles"],
            start=1
        ):
            _write_indent(
                file,
                3
            )

            file.write(
                f"TILE {index}\n"
            )

            _write_indent(
                file,
                3
            )

            file.write(
                "{\n"
            )

            _write_value(
                file,
                "ZOOM",
                tile["zoom"],
                4
            )

            _write_value(
                file,
                "X",
                tile["x"],
                4
            )

            _write_value(
                file,
                "Y",
                tile["y"],
                4
            )

            _write_value(
                file,
                "FILE",
                tile["file"],
                4
            )

            _write_indent(
                file,
                3
            )

            file.write(
                "}\n"
            )

        _write_indent(
            file,
            2
        )

        file.write(
            "}\n"
        )

        _write_indent(
            file,
            1
        )

        file.write(
            "}\n"
        )

        file.write(
            "}\n"
        )

    os.replace(
        temporary,
        output_path
    )

    return output_path


# ============================================================
# Public export API
# ============================================================

def export_teb(
    south,
    west,
    north,
    east,
    margin=0.02,
    satellite_zoom=SATELLITE_ZOOM,
    output_path=DEFAULT_TEB_FILE
):
    teb = build_teb(
        south,
        west,
        north,
        east,
        margin=margin,
        satellite_zoom=satellite_zoom
    )

    output_file = serialize_teb(
        teb,
        output_path
    )

    print()
    print("================================")
    print("      TEB export successful.")
    print("================================")
    print()

    print(
        f"DEM tiles:       "
        f"{len(teb['dem']['tiles'])}"
    )

    print(
        f"Satellite tiles: "
        f"{len(teb['satellite']['tiles'])}"
    )

    print(
        f"Elevation range: "
        f"{teb['dem']['statistics']['minimum_elevation_m']} "
        f"to "
        f"{teb['dem']['statistics']['maximum_elevation_m']} m"
    )

    print()
    print(
        "✓ DEM acquisition"
    )

    print(
        "✓ Satellite imagery acquisition"
    )

    print(
        "✓ Terrain coverage construction"
    )

    print(
        "✓ TrainGo Terrain Database generation"
    )

    print()
    print(
        "TEB written to:"
    )

    print(
        f"  {output_file}"
    )

    return teb