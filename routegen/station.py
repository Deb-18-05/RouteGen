import requests


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {
    "User-Agent": "RouteGen/0.1 (railway route generator)"
}


class Station:
    def __init__(self, name, latitude, longitude, osm_type, osm_id):
        self.name = name
        self.latitude = latitude
        self.longitude = longitude
        self.osm_type = osm_type
        self.osm_id = osm_id

    def __str__(self):
        return (
            f"{self.name}\n"
            f"  Coordinates: {self.latitude}, {self.longitude}\n"
            f"  OSM: {self.osm_type}/{self.osm_id}"
        )


def resolve_station(query):
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 10,
        "countrycodes": "in",
        "addressdetails": 1
    }

    response = requests.get(
        NOMINATIM_URL,
        params=params,
        headers=HEADERS,
        timeout=15
    )

    response.raise_for_status()

    results = response.json()

    if not results:
        raise ValueError(
            f"No location found for '{query}'."
        )

    # Prefer results that look like actual railway stations.
    station_results = []

    for result in results:
        result_type = result.get("type", "").lower()
        category = result.get("category", "").lower()

        if (
            result_type in {"station", "halt"}
            or category == "railway"
        ):
            station_results.append(result)

    if not station_results:
        raise ValueError(
            f"'{query}' was found, but no railway station "
            f"could be confidently identified."
        )

    result = station_results[0]

    address = result.get("address", {})

    name = (
        address.get("station")
        or address.get("halt")
        or result.get("display_name", query)
    )

    return Station(
        name=name,
        latitude=float(result["lat"]),
        longitude=float(result["lon"]),
        osm_type=result["osm_type"],
        osm_id=result["osm_id"]
    )