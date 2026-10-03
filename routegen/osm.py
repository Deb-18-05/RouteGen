import json
import time
from pathlib import Path

import requests


OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

HEADERS = {
    "User-Agent": "RouteGen/0.1 railway route generator"
}

ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_FILE = ROOT / "data" / "osm_raw.json"

MAX_RETRIES_PER_SERVER = 2


def build_railway_query(
    south,
    west,
    north,
    east
):
    """
    Build an Overpass query for railway infrastructure
    inside the supplied bounding box.

    Bounding box order:
        south, west, north, east
    """

    return f"""
[out:json][timeout:120];

(
    way["railway"]({south},{west},{north},{east});
    node["railway"]({south},{west},{north},{east});
);

out body;
>;
out skel qt;
"""


def download_railway_data(
    south,
    west,
    north,
    east
):
    """
    Download railway-related OSM data from Overpass.

    Uses multiple Overpass servers with retries.
    Saves the successful raw response locally.
    """

    query = build_railway_query(
        south,
        west,
        north,
        east
    )

    print("Connecting to OpenStreetMap Overpass...")
    print("Downloading railway data...")

    last_error = None

    for server_index, server_url in enumerate(
        OVERPASS_SERVERS,
        start=1
    ):

        print()
        print(
            f"Overpass server {server_index}/"
            f"{len(OVERPASS_SERVERS)}:"
        )
        print(f"  {server_url}")

        for attempt in range(
            1,
            MAX_RETRIES_PER_SERVER + 1
        ):

            print(
                f"Attempt {attempt}/"
                f"{MAX_RETRIES_PER_SERVER}..."
            )

            try:
                response = requests.post(
                    server_url,
                    data=query,
                    headers=HEADERS,
                    timeout=180
                )

                response.raise_for_status()

                data = response.json()

                print("✓ OSM data received")

                RAW_DATA_FILE.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                with open(
                    RAW_DATA_FILE,
                    "w",
                    encoding="utf-8"
                ) as file:
                    json.dump(
                        data,
                        file,
                        ensure_ascii=False,
                        indent=2
                    )

                print("✓ Raw OSM data saved to:")
                print(f"  {RAW_DATA_FILE}")

                return data

            except requests.RequestException as error:
                last_error = error

                print(
                    f"✗ Request failed: {error}"
                )

                if attempt < MAX_RETRIES_PER_SERVER:
                    print("Waiting before retry...")
                    time.sleep(3)

        print("Switching to next Overpass server...")

    raise RuntimeError(
        "All Overpass servers failed.\n"
        f"Last error: {last_error}"
    )


def count_elements(data):
    """
    Count returned OSM elements by type.
    """

    nodes = 0
    ways = 0
    relations = 0

    for element in data.get("elements", []):

        element_type = element.get("type")

        if element_type == "node":
            nodes += 1

        elif element_type == "way":
            ways += 1

        elif element_type == "relation":
            relations += 1

    return nodes, ways, relations