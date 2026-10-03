import math


def haversine_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    """
    Calculate geographic distance between two
    coordinates in metres.
    """

    earth_radius = 6371000.0

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    a = min(1.0, max(0.0, a))

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c


def calculate_route_length(
    path,
    nodes
):
    """
    Calculate the total geographic length of a route
    represented by an OSM node sequence.
    """

    total_distance = 0.0

    for i in range(len(path) - 1):

        node_a = nodes.get(path[i])
        node_b = nodes.get(path[i + 1])

        if node_a is None or node_b is None:
            raise ValueError(
                "Route contains an OSM node that is "
                "missing from the node index."
            )

        total_distance += haversine_distance(
            node_a["latitude"],
            node_a["longitude"],
            node_b["latitude"],
            node_b["longitude"]
        )

    return total_distance


def calculate_route_bounds(
    path,
    nodes
):
    """
    Calculate the geographic bounding box of the route.

    Returns:
        south, west, north, east
    """

    latitudes = []
    longitudes = []

    for node_id in path:

        node = nodes.get(node_id)

        if node is None:
            continue

        latitudes.append(node["latitude"])
        longitudes.append(node["longitude"])

    if not latitudes:
        raise ValueError(
            "Cannot calculate route bounds because "
            "the route contains no valid coordinates."
        )

    return (
        min(latitudes),
        min(longitudes),
        max(latitudes),
        max(longitudes)
    )


def find_largest_gaps(
    path,
    nodes,
    threshold_m=500.0,
    max_results=20
):
    """
    Find unusually large gaps between consecutive
    route nodes.

    Returns the largest gaps first.

    Each result contains:

        distance
        from_node
        to_node
        from_coordinate
        to_coordinate
    """

    gaps = []

    for i in range(len(path) - 1):

        from_id = path[i]
        to_id = path[i + 1]

        node_a = nodes.get(from_id)
        node_b = nodes.get(to_id)

        if node_a is None or node_b is None:
            continue

        distance = haversine_distance(
            node_a["latitude"],
            node_a["longitude"],
            node_b["latitude"],
            node_b["longitude"]
        )

        if distance >= threshold_m:

            gaps.append({
                "distance": distance,
                "from_node": from_id,
                "to_node": to_id,
                "from_coordinate": (
                    node_a["latitude"],
                    node_a["longitude"]
                ),
                "to_coordinate": (
                    node_b["latitude"],
                    node_b["longitude"]
                )
            })

    gaps.sort(
        key=lambda item: item["distance"],
        reverse=True
    )

    return gaps[:max_results]


def calculate_edge_statistics(
    path,
    nodes
):
    """
    Calculate basic statistics for consecutive
    route-node distances.
    """

    distances = []

    for i in range(len(path) - 1):

        node_a = nodes.get(path[i])
        node_b = nodes.get(path[i + 1])

        if node_a is None or node_b is None:
            continue

        distance = haversine_distance(
            node_a["latitude"],
            node_a["longitude"],
            node_b["latitude"],
            node_b["longitude"]
        )

        distances.append(distance)

    if not distances:
        return {
            "count": 0,
            "minimum": 0.0,
            "maximum": 0.0,
            "average": 0.0
        }

    return {
        "count": len(distances),
        "minimum": min(distances),
        "maximum": max(distances),
        "average": sum(distances) / len(distances)
    }


def validate_route(
    path,
    nodes,
    expected_start=None,
    expected_end=None,
    max_node_gap=1000.0
):
    """
    Perform diagnostic geometric and structural validation
    of a generated railway route.

    This version is deliberately strict about suspicious
    geometry so that RouteGen does not proceed into topology
    analysis with a questionable route.

    Current checks:

    1. Route exists.
    2. Route contains at least two nodes.
    3. Every route node has coordinates.
    4. No consecutive duplicate nodes.
    5. Large geographic gaps are reported.
    6. Route length is calculated.
    7. Geographic bounds are calculated.
    8. Edge statistics are calculated.

    Note:
        This validator does not yet know which OSM way
        created each graph edge. That belongs to the
        upcoming topology/data-model stage.
    """

    if not path:

        return {
            "valid": False,
            "errors": [
                "Route contains no nodes."
            ]
        }

    if len(path) < 2:

        return {
            "valid": False,
            "errors": [
                "Route must contain at least two nodes."
            ]
        }

    errors = []
    warnings = []

    missing_nodes = []

    for node_id in path:

        if node_id not in nodes:
            missing_nodes.append(node_id)

    if missing_nodes:

        errors.append(
            f"{len(missing_nodes)} route nodes are "
            "missing from the OSM node index."
        )

    duplicate_pairs = 0

    for i in range(len(path) - 1):

        if path[i] == path[i + 1]:
            duplicate_pairs += 1

    if duplicate_pairs:

        errors.append(
            f"{duplicate_pairs} consecutive duplicate "
            "node pairs detected."
        )

    if errors:

        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings
        }

    route_length = calculate_route_length(
        path,
        nodes
    )

    largest_gaps = find_largest_gaps(
        path,
        nodes,
        threshold_m=500.0,
        max_results=20
    )

    edge_statistics = calculate_edge_statistics(
        path,
        nodes
    )

    if largest_gaps:

        largest_gap = largest_gaps[0]

        if largest_gap["distance"] > max_node_gap:

            warnings.append(
                f"Largest consecutive-node gap is "
                f"{largest_gap['distance']:.2f} m."
            )

    south, west, north, east = (
        calculate_route_bounds(
            path,
            nodes
        )
    )

    first_node = nodes[path[0]]
    last_node = nodes[path[-1]]

    start_offset = None
    end_offset = None

    if expected_start is not None:

        start_offset = haversine_distance(
            expected_start[0],
            expected_start[1],
            first_node["latitude"],
            first_node["longitude"]
        )

    if expected_end is not None:

        end_offset = haversine_distance(
            expected_end[0],
            expected_end[1],
            last_node["latitude"],
            last_node["longitude"]
        )

    return {
        "valid": True,

        "errors": errors,
        "warnings": warnings,

        "node_count": len(path),

        "route_length_m": route_length,
        "route_length_km": route_length / 1000.0,

        "largest_gap_m": (
            largest_gaps[0]["distance"]
            if largest_gaps
            else 0.0
        ),

        "largest_gap_from": (
            largest_gaps[0]["from_node"]
            if largest_gaps
            else None
        ),

        "largest_gap_to": (
            largest_gaps[0]["to_node"]
            if largest_gaps
            else None
        ),

        "large_gaps": largest_gaps,

        "edge_count": edge_statistics["count"],
        "minimum_edge_m": edge_statistics["minimum"],
        "maximum_edge_m": edge_statistics["maximum"],
        "average_edge_m": edge_statistics["average"],

        "south": south,
        "west": west,
        "north": north,
        "east": east,

        "start_node": path[0],
        "end_node": path[-1],

        "start_coordinate": (
            first_node["latitude"],
            first_node["longitude"]
        ),

        "end_coordinate": (
            last_node["latitude"],
            last_node["longitude"]
        ),

        "start_offset_m": start_offset,
        "end_offset_m": end_offset
    }


def print_validation_report(
    report
):
    """
    Print a detailed human-readable validation report.
    """

    print()
    print("================================")
    print("       Route Validation")
    print("================================")
    print()

    if not report.get("valid", False):

        print("✗ ROUTE VALIDATION FAILED")
        print()

        for error in report.get(
            "errors",
            []
        ):

            print(
                f"ERROR: {error}"
            )

        return

    print("✓ Structural validation passed")
    print()

    print("Route statistics")
    print("----------------")

    print(
        f"Route nodes:        "
        f"{report['node_count']}"
    )

    print(
        f"Route length:       "
        f"{report['route_length_km']:.2f} km"
    )

    print(
        f"Average node gap:   "
        f"{report['average_edge_m']:.2f} m"
    )

    print(
        f"Maximum node gap:   "
        f"{report['maximum_edge_m']:.2f} m"
    )

    print()

    print("Route bounds")
    print("------------")

    print(
        f"South: {report['south']}"
    )

    print(
        f"West:  {report['west']}"
    )

    print(
        f"North: {report['north']}"
    )

    print(
        f"East:  {report['east']}"
    )

    print()

    print("Route endpoints")
    print("---------------")

    print(
        f"Start OSM node: {report['start_node']}"
    )

    print(
        f"End OSM node:   {report['end_node']}"
    )

    if report["start_offset_m"] is not None:

        print(
            f"Start offset:   "
            f"{report['start_offset_m']:.2f} m"
        )

    if report["end_offset_m"] is not None:

        print(
            f"End offset:     "
            f"{report['end_offset_m']:.2f} m"
        )

    print()

    large_gaps = report.get(
        "large_gaps",
        []
    )

    if large_gaps:

        print("Largest suspicious gaps")
        print("-----------------------")

        for index, gap in enumerate(
            large_gaps,
            start=1
        ):

            print(
                f"{index:02d}. "
                f"{gap['distance']:.2f} m"
            )

            print(
                f"    From: {gap['from_node']}"
            )

            print(
                f"    To:   {gap['to_node']}"
            )

            print(
                f"    From coordinate: "
                f"{gap['from_coordinate'][0]}, "
                f"{gap['from_coordinate'][1]}"
            )

            print(
                f"    To coordinate:   "
                f"{gap['to_coordinate'][0]}, "
                f"{gap['to_coordinate'][1]}"
            )

            print()

    else:

        print("Large gaps")
        print("-----------")
        print("None detected.")
        print()

    if report["warnings"]:

        print("Warnings")
        print("--------")

        for warning in report["warnings"]:

            print(
                f"⚠ {warning}"
            )

        print()

    else:

        print("Warnings")
        print("--------")
        print("None")
        print()

    print("✓ Route validation completed.")