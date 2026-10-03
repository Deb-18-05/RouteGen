import heapq
import math


def haversine_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    """
    Calculate the geographic distance between
    two coordinates in metres.
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


def find_nearest_graph_nodes(
    latitude,
    longitude,
    nodes,
    graph,
    radius_m=2000,
    max_candidates=20
):
    """
    Find multiple railway graph nodes near a station.

    Unlike the old function, this does NOT assume that
    the single geographically closest node is the correct
    railway attachment point.

    Returns:
        List of:
        (node_id, distance_in_metres)

    sorted from nearest to farthest.
    """

    candidates = []

    for node_id in graph:

        node = nodes.get(node_id)

        if node is None:
            continue

        distance = haversine_distance(
            latitude,
            longitude,
            node["latitude"],
            node["longitude"]
        )

        if distance <= radius_m:
            candidates.append(
                (node_id, distance)
            )

    candidates.sort(
        key=lambda item: item[1]
    )

    return candidates[:max_candidates]


def find_nearest_graph_node(
    latitude,
    longitude,
    nodes,
    graph
):
    """
    Compatibility wrapper.

    Returns the single nearest graph node.

    This function is retained so existing code does
    not break, but route generation should use
    find_nearest_graph_nodes().
    """

    candidates = find_nearest_graph_nodes(
        latitude,
        longitude,
        nodes,
        graph,
        radius_m=2000,
        max_candidates=1
    )

    if not candidates:
        raise ValueError(
            "No railway graph node found near station."
        )

    return candidates[0]


def _shortest_paths_to_targets(
    start_node,
    target_nodes,
    nodes,
    graph
):
    """Find shortest paths from one start to a set of target nodes."""

    if start_node not in graph:
        return {}, {}

    remaining_targets = set(target_nodes)

    if not remaining_targets:
        return {}, {}

    distances = {start_node: 0.0}
    previous = {}
    priority_queue = [(0.0, start_node)]

    while priority_queue:
        current_distance, current_node = heapq.heappop(priority_queue)

        if current_distance > distances.get(current_node, float("inf")):
            continue

        remaining_targets.discard(current_node)

        if not remaining_targets:
            break

        current_data = nodes.get(current_node)

        if current_data is None:
            continue

        for neighbour in graph.get(current_node, set()):
            neighbour_data = nodes.get(neighbour)

            if neighbour_data is None:
                continue

            edge_distance = haversine_distance(
                current_data["latitude"],
                current_data["longitude"],
                neighbour_data["latitude"],
                neighbour_data["longitude"]
            )
            new_distance = current_distance + edge_distance

            if new_distance < distances.get(neighbour, float("inf")):
                distances[neighbour] = new_distance
                previous[neighbour] = current_node
                heapq.heappush(priority_queue, (new_distance, neighbour))

    return distances, previous


def _reconstruct_path(start_node, end_node, previous):
    path = []
    current = end_node

    while current != start_node:
        if current not in previous:
            return None

        path.append(current)
        current = previous[current]

    path.append(start_node)
    path.reverse()
    return path


def shortest_path(
    start_node,
    end_node,
    nodes,
    graph
):
    """
    Find the shortest geographic path between two
    railway graph nodes using Dijkstra's algorithm.

    Edge weight is the geographic distance between
    connected OSM nodes.
    """

    if start_node not in graph:
        return None, None

    if end_node not in graph:
        return None, None

    distances, previous = _shortest_paths_to_targets(
        start_node,
        {end_node},
        nodes,
        graph
    )

    if end_node not in distances:
        return None, None

    path = _reconstruct_path(start_node, end_node, previous)

    return path, distances[end_node]


def find_route_between_stations(
    start_latitude,
    start_longitude,
    end_latitude,
    end_longitude,
    nodes,
    graph,
    radius_m=2000,
    max_candidates=20
):
    """
    Find a connected railway route between two stations.

    Multiple candidate graph nodes are considered at each
    station because the geographically nearest railway node
    may belong to a disconnected yard, siding, platform
    track, or other railway fragment.

    The function evaluates every candidate pair and keeps
    the shortest connected railway path.
    """

    start_candidates = find_nearest_graph_nodes(
        start_latitude,
        start_longitude,
        nodes,
        graph,
        radius_m=radius_m,
        max_candidates=max_candidates
    )

    end_candidates = find_nearest_graph_nodes(
        end_latitude,
        end_longitude,
        nodes,
        graph,
        radius_m=radius_m,
        max_candidates=max_candidates
    )

    if not start_candidates:
        raise ValueError(
            "No railway graph candidates found near "
            "the starting station."
        )

    if not end_candidates:
        raise ValueError(
            "No railway graph candidates found near "
            "the ending station."
        )

    print(
        f"Starting station candidates: "
        f"{len(start_candidates)}"
    )

    print(
        f"Ending station candidates: "
        f"{len(end_candidates)}"
    )

    best_path = None
    best_distance = float("inf")
    best_start_node = None
    best_end_node = None

    tested_pairs = 0
    connected_pairs = 0

    end_node_ids = {
        node_id
        for node_id, _ in end_candidates
    }

    for start_node, start_distance in (
        start_candidates
    ):

        distances, previous = _shortest_paths_to_targets(
            start_node,
            end_node_ids,
            nodes,
            graph
        )

        for end_node, end_distance in (
            end_candidates
        ):

            tested_pairs += 1

            route_distance = distances.get(end_node)

            if route_distance is None:
                continue

            connected_pairs += 1

            if route_distance < best_distance:

                best_path = _reconstruct_path(
                    start_node,
                    end_node,
                    previous
                )
                best_distance = route_distance

                best_start_node = start_node
                best_end_node = end_node

    print(
        f"Candidate pairs tested: {tested_pairs}"
    )

    print(
        f"Connected candidate pairs: "
        f"{connected_pairs}"
    )

    if best_path is None:
        return None

    return {
        "path": best_path,
        "distance": best_distance,
        "start_node": best_start_node,
        "end_node": best_end_node,
        "start_candidates": start_candidates,
        "end_candidates": end_candidates
    }