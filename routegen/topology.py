import math
from collections import Counter, defaultdict


# ============================================================
# Railway Topology Engine v1.2
# ============================================================
#
# Design principles
# -----------------
#
# 1. edge_metadata is authoritative for route membership.
# 2. OSM way transitions are NOT automatically physical
#    crossovers.
# 3. Individual railway ways are NEVER destructively merged.
# 4. Individual topology contacts are NEVER discarded.
# 5. Geometry is treated as UNDIRECTED railway orientation.
# 6. Structured railway/service tags outrank names/refs.
# 7. Conservative topology is preferred over false topology.
# 8. Connected infrastructure is retained beyond the exact
#    Dijkstra route.
#
# Public API:
#
#     analyze_route_topology(
#         path,
#         nodes,
#         graph,
#         edge_metadata,
#         ways,
#         node_way_index
#     )
#
#     print_topology_report(topology)
#
# ============================================================


ENGINE_VERSION = "1.2"


# ============================================================
# CONSTANTS
# ============================================================

ALIGNED_ANGLE_MAX = 3.0
DIVERGENT_ANGLE_MAX = 12.0
STRONG_DIVERGENCE_MAX = 45.0
CROSSING_ANGLE_MIN = 75.0

SHORT_GEOMETRY_DISTANCE_M = 250.0
PARALLEL_ANGLE_MAX = 3.0

MAX_INFRASTRUCTURE_DISCOVERY_DEPTH = 4


# ============================================================
# Basic geometry
# ============================================================

def calculate_bearing(
    lat1,
    lon1,
    lat2,
    lon2
):
    """
    Calculate forward bearing from point 1 to point 2.

    Returns:
        0 <= bearing < 360
    """

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    delta_lon = math.radians(
        lon2 - lon1
    )

    x = (
        math.sin(delta_lon)
        * math.cos(lat2_rad)
    )

    y = (
        math.cos(lat1_rad)
        * math.sin(lat2_rad)
        -
        math.sin(lat1_rad)
        * math.cos(lat2_rad)
        * math.cos(delta_lon)
    )

    bearing = math.degrees(
        math.atan2(x, y)
    )

    return (
        bearing + 360.0
    ) % 360.0


def undirected_angle_difference(
    bearing_a,
    bearing_b
):
    """
    Compare physical track orientations.

    Direction does not matter.

    Therefore:
        0°   -> same orientation
        180° -> also same orientation
        90°  -> perpendicular
    """

    difference = abs(
        bearing_a - bearing_b
    ) % 180.0

    return min(
        difference,
        180.0 - difference
    )


def calculate_orientation_difference(
    bearing_a,
    bearing_b
):
    return undirected_angle_difference(
        bearing_a,
        bearing_b
    )


def haversine_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    earth_radius = 6371000.0

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    delta_lat = math.radians(
        lat2 - lat1
    )

    delta_lon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(delta_lat / 2.0) ** 2
        +
        math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2.0) ** 2
    )

    c = 2.0 * math.atan2(
        math.sqrt(a),
        math.sqrt(
            max(
                0.0,
                1.0 - a
            )
        )
    )

    return earth_radius * c


# ============================================================
# Node / way geometry
# ============================================================

def calculate_route_orientation(
    previous_node,
    current_node,
    nodes
):
    previous = nodes.get(
        previous_node
    )

    current = nodes.get(
        current_node
    )

    if previous is None or current is None:
        return None

    return calculate_bearing(
        previous["latitude"],
        previous["longitude"],
        current["latitude"],
        current["longitude"]
    )


def get_way_node_position(
    way_data,
    node_id
):
    node_sequence = way_data.get(
        "nodes",
        []
    )

    if node_id not in node_sequence:
        return None

    index = node_sequence.index(
        node_id
    )

    if (
        index == 0
        or
        index == len(node_sequence) - 1
    ):
        return "endpoint"

    return "interior"


def way_is_endpoint(
    way_id,
    node_id,
    ways
):
    way_data = ways.get(
        way_id
    )

    if way_data is None:
        return False

    return (
        get_way_node_position(
            way_data,
            node_id
        )
        == "endpoint"
    )


def classify_way_position(
    way_id,
    node_id,
    ways
):
    position = get_way_node_position(
        ways.get(
            way_id,
            {}
        ),
        node_id
    )

    return position or "unknown"


def get_way_neighbour_nodes(
    way_data,
    node_id
):
    sequence = way_data.get(
        "nodes",
        []
    )

    if node_id not in sequence:
        return []

    index = sequence.index(
        node_id
    )

    neighbours = []

    if index > 0:
        neighbours.append(
            sequence[index - 1]
        )

    if index < len(sequence) - 1:
        neighbours.append(
            sequence[index + 1]
        )

    return neighbours


def calculate_way_orientation_at_node(
    way_data,
    node_id,
    nodes
):
    """
    Estimate physical orientation of an OSM way at a node.

    If the node is in the middle of a way, calculate both
    directions and convert them to an undirected orientation.
    """

    neighbours = get_way_neighbour_nodes(
        way_data,
        node_id
    )

    if not neighbours:
        return None

    current = nodes.get(
        node_id
    )

    if current is None:
        return None

    bearings = []

    for neighbour_id in neighbours:
        neighbour = nodes.get(
            neighbour_id
        )

        if neighbour is None:
            continue

        bearings.append(
            calculate_bearing(
                current["latitude"],
                current["longitude"],
                neighbour["latitude"],
                neighbour["longitude"]
            )
        )

    if not bearings:
        return None

    if len(bearings) == 1:
        return bearings[0]

    # Two directions on the same physical track are normally
    # approximately 180 degrees apart.
    #
    # Convert them into an orientation in [0, 180).
    #
    # Using the first bearing is sufficient because all later
    # angle comparisons are undirected.
    return bearings[0] % 180.0


def calculate_way_local_geometry(
    way_data,
    node_id,
    nodes
):
    """
    Return local geometry around a topology node.

    This provides more information than a single bearing.
    """

    neighbours = get_way_neighbour_nodes(
        way_data,
        node_id
    )

    orientations = []

    for neighbour_id in neighbours:
        node = nodes.get(
            node_id
        )

        neighbour = nodes.get(
            neighbour_id
        )

        if node is None or neighbour is None:
            continue

        bearing = calculate_bearing(
            node["latitude"],
            node["longitude"],
            neighbour["latitude"],
            neighbour["longitude"]
        )

        distance = haversine_distance(
            node["latitude"],
            node["longitude"],
            neighbour["latitude"],
            neighbour["longitude"]
        )

        orientations.append(
            {
                "node_id": neighbour_id,
                "bearing": bearing,
                "distance_m": distance
            }
        )

    return orientations


# ============================================================
# Route edge extraction
# ============================================================

def build_route_edges(
    path
):
    edges = set()

    for index in range(
        len(path) - 1
    ):
        node_a = path[index]
        node_b = path[index + 1]

        edges.add(
            tuple(
                sorted(
                    (
                        node_a,
                        node_b
                    )
                )
            )
        )

    return edges


def build_route_way_ids(
    route_edges,
    edge_metadata
):
    """
    edge_metadata is the authoritative source for route-way
    membership.
    """

    route_way_ids = set()

    for edge in route_edges:
        metadata = edge_metadata.get(
            edge
        )

        if metadata is None:
            continue

        for way_id in metadata.get(
            "ways",
            set()
        ):
            route_way_ids.add(
                way_id
            )

    return route_way_ids


def build_route_way_ids_by_node(
    path,
    edge_metadata
):
    result = defaultdict(set)

    for index in range(
        len(path) - 1
    ):
        node_a = path[index]
        node_b = path[index + 1]

        edge = tuple(
            sorted(
                (
                    node_a,
                    node_b
                )
            )
        )

        metadata = edge_metadata.get(
            edge
        )

        if metadata is None:
            continue

        for way_id in metadata.get(
            "ways",
            set()
        ):
            result[node_a].add(
                way_id
            )

            result[node_b].add(
                way_id
            )

    return dict(result)


# ============================================================
# Connected railway way relationships
# ============================================================

def get_connected_way_ids(
    node_id,
    node_way_index
):
    return set(
        node_way_index.get(
            node_id,
            set()
        )
    )


def get_adjacent_way_ids(
    node_id,
    route_way_ids_by_node,
    node_way_index
):
    connected = get_connected_way_ids(
        node_id,
        node_way_index
    )

    route_ways = set(
        route_way_ids_by_node.get(
            node_id,
            set()
        )
    )

    return connected - route_ways


# ============================================================
# Railway role classification
# ============================================================

def get_way_role(
    way_data
):
    """
    Conservative railway role classifier.

    Authority:
        railway tag
        service tag

    Never use:
        name
        ref
        usage

    to decide siding/yard status.
    """

    tags = way_data.get(
        "tags",
        {}
    )

    railway = str(
        tags.get(
            "railway",
            ""
        )
    ).strip().lower()

    service = str(
        tags.get(
            "service",
            ""
        )
    ).strip().lower()

    if railway == "siding":
        return "siding"

    if service == "siding":
        return "siding"

    if railway in {
        "yard",
        "yard_track"
    }:
        return "yard"

    if service == "yard":
        return "yard"

    if railway in {
        "spur",
        "branch"
    }:
        return "branch"

    if service in {
        "spur",
        "branch"
    }:
        return "branch"

    if railway == "rail":
        return "rail"

    if railway:
        return railway

    return "unknown"


def get_way_roles(
    way_ids,
    ways
):
    result = {}

    for way_id in way_ids:
        result[way_id] = get_way_role(
            ways.get(
                way_id,
                {}
            )
        )

    return result


# ============================================================
# Tag inspection
# ============================================================

def inspect_way_pair_tags(
    route_way,
    adjacent_way
):
    route_tags = route_way.get(
        "tags",
        {}
    )

    adjacent_tags = adjacent_way.get(
        "tags",
        {}
    )

    evidence = []

    route_railway = str(
        route_tags.get(
            "railway",
            ""
        )
    ).strip().lower()

    adjacent_railway = str(
        adjacent_tags.get(
            "railway",
            ""
        )
    ).strip().lower()

    route_service = str(
        route_tags.get(
            "service",
            ""
        )
    ).strip().lower()

    adjacent_service = str(
        adjacent_tags.get(
            "service",
            ""
        )
    ).strip().lower()

    if (
        adjacent_railway == "siding"
        or
        adjacent_service == "siding"
    ):
        evidence.append(
            "adjacent_siding"
        )

    if (
        adjacent_railway in {
            "yard",
            "yard_track"
        }
        or
        adjacent_service == "yard"
    ):
        evidence.append(
            "adjacent_yard"
        )

    if (
        adjacent_railway in {
            "spur",
            "branch"
        }
        or
        adjacent_service in {
            "spur",
            "branch"
        }
    ):
        evidence.append(
            "adjacent_branch"
        )

    if (
        route_service
        and adjacent_service
        and route_service != adjacent_service
    ):
        evidence.append(
            "different_service_roles"
        )

    if (
        route_railway
        and adjacent_railway
        and route_railway != adjacent_railway
    ):
        evidence.append(
            "different_railway_types"
        )

    return evidence


# ============================================================
# Geometry classification
# ============================================================

def classify_connection_geometry(
    node_id,
    route_way_id,
    adjacent_way_id,
    nodes,
    ways
):
    route_way = ways.get(
        route_way_id
    )

    adjacent_way = ways.get(
        adjacent_way_id
    )

    if (
        route_way is None
        or adjacent_way is None
    ):
        return {
            "classification": "unknown",
            "angle": None,
            "route_orientation": None,
            "adjacent_orientation": None,
            "parallel": False,
            "crossing_candidate": False
        }

    route_orientation = (
        calculate_way_orientation_at_node(
            route_way,
            node_id,
            nodes
        )
    )

    adjacent_orientation = (
        calculate_way_orientation_at_node(
            adjacent_way,
            node_id,
            nodes
        )
    )

    if (
        route_orientation is None
        or
        adjacent_orientation is None
    ):
        return {
            "classification": "unknown",
            "angle": None,
            "route_orientation": route_orientation,
            "adjacent_orientation": adjacent_orientation,
            "parallel": False,
            "crossing_candidate": False
        }

    angle = undirected_angle_difference(
        route_orientation,
        adjacent_orientation
    )

    if angle <= ALIGNED_ANGLE_MAX:
        classification = "aligned"
        parallel = True

    elif angle <= DIVERGENT_ANGLE_MAX:
        classification = "divergent"
        parallel = False

    elif angle <= STRONG_DIVERGENCE_MAX:
        classification = "strongly_divergent"
        parallel = False

    else:
        classification = "crossing_or_complex"
        parallel = False

    return {
        "classification": classification,
        "angle": angle,
        "route_orientation": route_orientation,
        "adjacent_orientation": adjacent_orientation,
        "parallel": parallel,
        "crossing_candidate": (
            angle >= CROSSING_ANGLE_MIN
        )
    }


# ============================================================
# Connection evidence
# ============================================================

def build_connection_evidence(
    node_id,
    route_way_id,
    adjacent_way_id,
    route_way_ids_by_node,
    ways,
    nodes
):
    route_way = ways.get(
        route_way_id,
        {}
    )

    adjacent_way = ways.get(
        adjacent_way_id,
        {}
    )

    evidence = set(
        inspect_way_pair_tags(
            route_way,
            adjacent_way
        )
    )

    route_position = (
        classify_way_position(
            route_way_id,
            node_id,
            ways
        )
    )

    adjacent_position = (
        classify_way_position(
            adjacent_way_id,
            node_id,
            ways
        )
    )

    if adjacent_position == "endpoint":
        evidence.add(
            "adjacent_way_endpoint"
        )

    if (
        route_position == "endpoint"
        and
        adjacent_position == "endpoint"
    ):
        evidence.add(
            "both_way_endpoints"
        )

    if route_position == "endpoint":
        evidence.add(
            "route_way_endpoint"
        )

    route_way_count = len(
        route_way_ids_by_node.get(
            node_id,
            set()
        )
    )

    if route_way_count > 1:
        evidence.add(
            "multiple_route_ways"
        )

    return (
        evidence,
        route_position,
        adjacent_position
    )


# ============================================================
# Local topology context
# ============================================================

def inspect_local_topology_context(
    node_id,
    route_way_ids,
    adjacent_way_ids,
    node_way_index,
    ways
):
    """
    Gather information about everything meeting at the node.

    This is deliberately retained in the topology object so
    future TDB logic can make better decisions without rerunning
    OSM analysis.
    """

    all_way_ids = set(
        node_way_index.get(
            node_id,
            set()
        )
    )

    roles = {}

    for way_id in all_way_ids:
        roles[way_id] = get_way_role(
            ways.get(
                way_id,
                {}
            )
        )

    return {
        "all_way_ids": sorted(
            all_way_ids
        ),
        "route_way_count": len(
            route_way_ids
        ),
        "adjacent_way_count": len(
            adjacent_way_ids
        ),
        "total_way_count": len(
            all_way_ids
        ),
        "roles": roles
    }


# ============================================================
# Individual connection record
# ============================================================

def build_connection_record(
    node_id,
    route_way_id,
    adjacent_way_id,
    nodes,
    ways,
    route_way_ids_by_node
):
    route_way = ways.get(
        route_way_id
    )

    adjacent_way = ways.get(
        adjacent_way_id
    )

    if (
        route_way is None
        or
        adjacent_way is None
    ):
        return None

    (
        evidence,
        route_position,
        adjacent_position
    ) = build_connection_evidence(
        node_id,
        route_way_id,
        adjacent_way_id,
        route_way_ids_by_node,
        ways,
        nodes
    )

    geometry = (
        classify_connection_geometry(
            node_id,
            route_way_id,
            adjacent_way_id,
            nodes,
            ways
        )
    )

    route_role = get_way_role(
        route_way
    )

    adjacent_role = get_way_role(
        adjacent_way
    )

    if route_role != adjacent_role:
        evidence.add(
            "different_way_roles"
        )

    record = {
        "node_id": node_id,
        "route_way_id": route_way_id,
        "adjacent_way_id": adjacent_way_id,
        "route_role": route_role,
        "adjacent_role": adjacent_role,
        "route_position": route_position,
        "adjacent_position": adjacent_position,
        "geometry": geometry,
        "evidence": sorted(
            evidence
        )
    }

    return record


# ============================================================
# Connection scoring
# ============================================================

def calculate_connection_score(
    record
):
    score = 0

    evidence = set(
        record.get(
            "evidence",
            []
        )
    )

    route_role = record.get(
        "route_role"
    )

    adjacent_role = record.get(
        "adjacent_role"
    )

    geometry = record.get(
        "geometry",
        {}
    )

    angle = geometry.get(
        "angle"
    )

    geometry_type = geometry.get(
        "classification"
    )

    # --------------------------------------------------------
    # Strong semantic evidence
    # --------------------------------------------------------

    if "adjacent_siding" in evidence:
        score += 6

    if "adjacent_yard" in evidence:
        score += 6

    if "adjacent_branch" in evidence:
        score += 5

    # --------------------------------------------------------
    # Strong structural evidence
    # --------------------------------------------------------

    if "both_way_endpoints" in evidence:
        score += 4

    if "adjacent_way_endpoint" in evidence:
        score += 2

    if "route_way_endpoint" in evidence:
        score += 1

    if "multiple_route_ways" in evidence:
        score += 1

    # --------------------------------------------------------
    # Role evidence
    # --------------------------------------------------------

    if (
        route_role != adjacent_role
        and
        route_role != "unknown"
        and
        adjacent_role != "unknown"
    ):
        score += 1

    if "different_service_roles" in evidence:
        score += 1

    # --------------------------------------------------------
    # Geometry evidence
    # --------------------------------------------------------

    if geometry_type == "aligned":
        score += 1

    elif geometry_type == "divergent":
        score += 2

    elif geometry_type == "strongly_divergent":
        score += 2

    elif geometry_type == "crossing_or_complex":
        score -= 3

    # --------------------------------------------------------
    # Geometry sanity penalty
    # --------------------------------------------------------

    if (
        angle is not None
        and
        angle > CROSSING_ANGLE_MIN
    ):
        score -= 3

    return score


def score_to_confidence(
    score
):
    if score >= 9:
        return "HIGH"

    if score >= 5:
        return "MEDIUM"

    return "LOW"


# ============================================================
# Physical connection judgement
# ============================================================

def judge_physical_connection(
    record
):
    """
    Conservative physical connection judgement.

    TRUE:
        There is meaningful structural/tag evidence.

    UNCERTAIN:
        Geometry or OSM structure suggests a possible connection,
        but the evidence is insufficient.

    FALSE:
        Explicitly rejected topology.

    The engine deliberately avoids inventing FALSE values unless
    the geometry is clearly inconsistent with a connection.
    """

    evidence = set(
        record.get(
            "evidence",
            []
        )
    )

    route_role = record.get(
        "route_role"
    )

    adjacent_role = record.get(
        "adjacent_role"
    )

    geometry = record.get(
        "geometry",
        {}
    )

    geometry_type = geometry.get(
        "classification"
    )

    score = record.get(
        "score",
        0
    )

    # --------------------------------------------------------
    # Explicit railway infrastructure
    # --------------------------------------------------------

    if (
        "adjacent_siding" in evidence
        or
        "adjacent_yard" in evidence
        or
        "adjacent_branch" in evidence
    ):
        return "TRUE"

    # --------------------------------------------------------
    # Both OSM ways terminate at the same node.
    #
    # This is strong evidence of an actual connected endpoint,
    # even if both ways are generic railway=rail.
    # --------------------------------------------------------

    if (
        "both_way_endpoints" in evidence
        and
        geometry_type != "crossing_or_complex"
    ):
        return "TRUE"

    # --------------------------------------------------------
    # Route endpoint meets an adjacent railway endpoint.
    # --------------------------------------------------------

    if (
        "route_way_endpoint" in evidence
        and
        "adjacent_way_endpoint" in evidence
        and
        geometry_type in {
            "aligned",
            "divergent",
            "strongly_divergent"
        }
    ):
        return "TRUE"

    # --------------------------------------------------------
    # Multiple route ways are not enough by themselves.
    # --------------------------------------------------------

    if (
        "multiple_route_ways" in evidence
        and
        "adjacent_way_endpoint" in evidence
        and
        geometry_type != "crossing_or_complex"
        and
        score >= 5
    ):
        return "TRUE"

    # --------------------------------------------------------
    # Generic rail-to-rail endpoint contact.
    #
    # We now require useful evidence instead of declaring every
    # shared-node contact true.
    # --------------------------------------------------------

    if (
        "adjacent_way_endpoint" in evidence
        and
        geometry_type in {
            "aligned",
            "divergent",
            "strongly_divergent"
        }
        and
        score >= 4
    ):
        return "TRUE"

    # --------------------------------------------------------
    # Crossing / complex geometry is never automatically TRUE.
    # --------------------------------------------------------

    if geometry_type == "crossing_or_complex":
        return "UNCERTAIN"

    # --------------------------------------------------------
    # Strongly divergent generic rail-to-rail geometry without
    # structural evidence remains uncertain.
    # --------------------------------------------------------

    if (
        route_role == "rail"
        and
        adjacent_role == "rail"
        and
        geometry_type in {
            "divergent",
            "strongly_divergent"
        }
    ):
        return "UNCERTAIN"

    # --------------------------------------------------------
    # Low-evidence generic contacts.
    # --------------------------------------------------------

    if score < 4:
        return "UNCERTAIN"

    return "UNCERTAIN"


# ============================================================
# Node classification
# ============================================================

def classify_topology_node(
    node_id,
    route_way_ids,
    adjacent_way_ids,
    records
):
    """
    Classify topology WITHOUT confusing OSM way transitions with
    physical railway topology.
    """

    if not adjacent_way_ids:
        if len(route_way_ids) >= 2:
            return "way_transition"

        return "normal"

    adjacent_roles = {
        record.get(
            "adjacent_role"
        )
        for record in records
    }

    geometry_types = {
        record.get(
            "geometry",
            {}
        ).get(
            "classification"
        )
        for record in records
    }

    physical_results = {
        record.get(
            "physical"
        )
        for record in records
    }

    # --------------------------------------------------------
    # Semantic infrastructure first.
    # --------------------------------------------------------

    if "siding" in adjacent_roles:
        return "siding_connection"

    if "yard" in adjacent_roles:
        return "yard_connection"

    if "branch" in adjacent_roles:
        return "branch_connection"

    # --------------------------------------------------------
    # Strong physical junction candidate.
    # --------------------------------------------------------

    if (
        len(adjacent_way_ids) >= 2
        and
        (
            "strongly_divergent"
            in geometry_types
            or
            "crossing_or_complex"
            in geometry_types
        )
    ):
        return "junction_candidate"

    # --------------------------------------------------------
    # Crossover candidate.
    #
    # This requires geometry suggesting a crossing/complex
    # relationship. Multiple OSM ways alone are insufficient.
    # --------------------------------------------------------

    if (
        len(route_way_ids) >= 2
        and
        (
            "crossing_or_complex"
            in geometry_types
        )
    ):
        return "crossover_candidate"

    # --------------------------------------------------------
    # Generic physical connection.
    # --------------------------------------------------------

    if "TRUE" in physical_results:
        return "connection"

    # --------------------------------------------------------
    # Possible but unresolved topology.
    # --------------------------------------------------------

    if "UNCERTAIN" in physical_results:
        return "ambiguous"

    # --------------------------------------------------------
    # OSM way transition.
    # --------------------------------------------------------

    if len(route_way_ids) >= 2:
        return "way_transition"

    return "ambiguous"


# ============================================================
# Topology point extraction
# ============================================================

def build_topology_points(
    path,
    nodes,
    graph,
    edge_metadata,
    ways,
    node_way_index
):
    route_way_ids_by_node = (
        build_route_way_ids_by_node(
            path,
            edge_metadata
        )
    )

    topology_points = []
    connection_records = []

    connection_counter = 1

    for node_id in path:

        route_way_ids = set(
            route_way_ids_by_node.get(
                node_id,
                set()
            )
        )

        connected_way_ids = (
            get_connected_way_ids(
                node_id,
                node_way_index
            )
        )

        adjacent_way_ids = (
            connected_way_ids
            -
            route_way_ids
        )

        records = []

        for route_way_id in sorted(
            route_way_ids
        ):
            for adjacent_way_id in sorted(
                adjacent_way_ids
            ):

                record = build_connection_record(
                    node_id,
                    route_way_id,
                    adjacent_way_id,
                    nodes,
                    ways,
                    route_way_ids_by_node
                )

                if record is None:
                    continue

                record["connection_id"] = (
                    f"C{connection_counter:04d}"
                )

                connection_counter += 1

                record["score"] = (
                    calculate_connection_score(
                        record
                    )
                )

                record["confidence"] = (
                    score_to_confidence(
                        record["score"]
                    )
                )

                record["physical"] = (
                    judge_physical_connection(
                        record
                    )
                )

                records.append(
                    record
                )

                connection_records.append(
                    record
                )

        is_endpoint = (
            node_id == path[0]
            or
            node_id == path[-1]
        )

        has_way_transition = (
            len(route_way_ids) >= 2
        )

        has_adjacent_infrastructure = (
            bool(adjacent_way_ids)
        )

        is_topology_point = (
            is_endpoint
            or
            has_way_transition
            or
            has_adjacent_infrastructure
        )

        if not is_topology_point:
            continue

        classification = (
            classify_topology_node(
                node_id,
                route_way_ids,
                adjacent_way_ids,
                records
            )
        )

        context = (
            inspect_local_topology_context(
                node_id,
                route_way_ids,
                adjacent_way_ids,
                node_way_index,
                ways
            )
        )

        topology_points.append(
            {
                "node_id": node_id,
                "route_way_ids": sorted(
                    route_way_ids
                ),
                "connected_way_ids": sorted(
                    connected_way_ids
                ),
                "adjacent_way_ids": sorted(
                    adjacent_way_ids
                ),
                "classification": classification,
                "is_endpoint": is_endpoint,
                "context": context,
                "connections": records
            }
        )

    return (
        topology_points,
        connection_records
    )


# ============================================================
# Route way transitions
# ============================================================

def build_route_way_transitions(
    path,
    edge_metadata
):
    transitions = []

    if len(path) < 2:
        return transitions

    previous_way_ids = None

    for index in range(
        len(path) - 1
    ):
        node_a = path[index]
        node_b = path[index + 1]

        edge = tuple(
            sorted(
                (
                    node_a,
                    node_b
                )
            )
        )

        metadata = edge_metadata.get(
            edge,
            {}
        )

        current_way_ids = set(
            metadata.get(
                "ways",
                set()
            )
        )

        if (
            previous_way_ids is not None
            and
            current_way_ids
            !=
            previous_way_ids
        ):
            transitions.append(
                {
                    "node_id": node_a,
                    "from_ways": sorted(
                        previous_way_ids
                    ),
                    "to_ways": sorted(
                        current_way_ids
                    )
                }
            )

        previous_way_ids = (
            current_way_ids
        )

    return transitions


# ============================================================
# Connected infrastructure
# ============================================================

def build_connected_infrastructure(
    route_way_ids,
    ways,
    node_way_index,
    max_depth=MAX_INFRASTRUCTURE_DISCOVERY_DEPTH
):
    """
    Breadth-first discovery of connected railway ways.

    Every OSM way remains individually represented.
    """

    connected = set(
        route_way_ids
    )

    depth_map = {
        way_id: 0
        for way_id in route_way_ids
    }

    frontier = set(
        route_way_ids
    )

    for depth in range(
        1,
        max_depth + 1
    ):
        next_frontier = set()

        for way_id in frontier:

            way_data = ways.get(
                way_id
            )

            if way_data is None:
                continue

            for node_id in way_data.get(
                "nodes",
                []
            ):

                neighbouring_ways = (
                    node_way_index.get(
                        node_id,
                        set()
                    )
                )

                for adjacent_way_id in (
                    neighbouring_ways
                ):
                    if adjacent_way_id in connected:
                        continue

                    connected.add(
                        adjacent_way_id
                    )

                    depth_map[
                        adjacent_way_id
                    ] = depth

                    next_frontier.add(
                        adjacent_way_id
                    )

        frontier = next_frontier

        if not frontier:
            break

    return {
        "way_ids": connected,
        "depth_map": depth_map,
        "max_depth": max(
            depth_map.values(),
            default=0
        )
    }


# ============================================================
# Infrastructure statistics
# ============================================================

def calculate_infrastructure_statistics(
    connected_infrastructure,
    ways
):
    role_counts = Counter()
    railway_type_counts = Counter()

    for way_id in connected_infrastructure.get(
        "way_ids",
        set()
    ):
        way_data = ways.get(
            way_id,
            {}
        )

        role_counts[
            get_way_role(
                way_data
            )
        ] += 1

        railway_type = (
            way_data.get(
                "tags",
                {}
            ).get(
                "railway",
                "unknown"
            )
        )

        railway_type_counts[
            railway_type
        ] += 1

    return {
        "role_counts": dict(
            role_counts
        ),
        "railway_type_counts": dict(
            railway_type_counts
        )
    }


# ============================================================
# Route segmentation
# ============================================================

def build_route_segments(
    path,
    edge_metadata
):
    """
    Split the route at OSM way identity changes.

    These are representation segments, NOT necessarily physical
    topology boundaries.
    """

    if len(path) < 2:
        return []

    segments = []

    current_nodes = [
        path[0]
    ]

    current_way_ids = None

    for index in range(
        len(path) - 1
    ):
        node_a = path[index]
        node_b = path[index + 1]

        edge = tuple(
            sorted(
                (
                    node_a,
                    node_b
                )
            )
        )

        metadata = edge_metadata.get(
            edge,
            {}
        )

        way_ids = set(
            metadata.get(
                "ways",
                set()
            )
        )

        if current_way_ids is None:
            current_way_ids = way_ids

        elif way_ids != current_way_ids:

            segments.append(
                {
                    "nodes": current_nodes,
                    "way_ids": sorted(
                        current_way_ids
                    )
                }
            )

            current_nodes = [
                node_a
            ]

            current_way_ids = way_ids

        current_nodes.append(
            node_b
        )

    if current_nodes:
        segments.append(
            {
                "nodes": current_nodes,
                "way_ids": sorted(
                    current_way_ids or set()
                )
            }
        )

    return segments


# ============================================================
# Connection groups
# ============================================================

def build_connection_groups(
    connection_records
):
    """
    Reporting-only grouping.

    Individual connection records remain authoritative.
    """

    groups = defaultdict(list)

    for record in connection_records:
        groups[
            record["node_id"]
        ].append(
            record
        )

    result = []

    for node_id, records in groups.items():
        result.append(
            {
                "node_id": node_id,
                "connection_ids": [
                    record["connection_id"]
                    for record in records
                ],
                "connection_count": len(
                    records
                )
            }
        )

    return result


# ============================================================
# Duplicate / anomaly detection
# ============================================================

def detect_duplicate_connections(
    connection_records
):
    seen = set()
    duplicates = []

    for record in connection_records:

        key = (
            record["node_id"],
            record["route_way_id"],
            record["adjacent_way_id"]
        )

        if key in seen:
            duplicates.append(
                record["connection_id"]
            )

        else:
            seen.add(
                key
            )

    return duplicates


def detect_topology_anomalies(
    topology
):
    anomalies = []

    points = topology.get(
        "topology_points",
        []
    )

    connections = topology.get(
        "connections",
        []
    )

    # --------------------------------------------------------
    # Generic low-evidence rail contacts
    # --------------------------------------------------------

    for record in connections:

        if (
            record.get(
                "route_role"
            ) == "rail"
            and
            record.get(
                "adjacent_role"
            ) == "rail"
            and
            record.get(
                "physical"
            ) == "UNCERTAIN"
        ):
            anomalies.append(
                {
                    "type": "uncertain_rail_contact",
                    "connection_id": record[
                        "connection_id"
                    ],
                    "node_id": record[
                        "node_id"
                    ]
                }
            )

    # --------------------------------------------------------
    # Complex geometry
    # --------------------------------------------------------

    for record in connections:

        geometry = record.get(
            "geometry",
            {}
        )

        if geometry.get(
            "classification"
        ) == "crossing_or_complex":

            anomalies.append(
                {
                    "type": "complex_geometry",
                    "connection_id": record[
                        "connection_id"
                    ],
                    "node_id": record[
                        "node_id"
                    ]
                }
            )

    # --------------------------------------------------------
    # Huge topology fan-out
    # --------------------------------------------------------

    for point in points:

        connected_count = len(
            point.get(
                "connected_way_ids",
                []
            )
        )

        if connected_count >= 8:

            anomalies.append(
                {
                    "type": "high_way_fanout",
                    "node_id": point[
                        "node_id"
                    ],
                    "way_count": connected_count
                }
            )

    return anomalies


# ============================================================
# Statistics
# ============================================================

def calculate_topology_statistics(
    topology
):
    topology_points = topology.get(
        "topology_points",
        []
    )

    connections = topology.get(
        "connections",
        []
    )

    route_segments = topology.get(
        "route_segments",
        []
    )

    connected_infrastructure = topology.get(
        "connected_infrastructure",
        {}
    )

    classification_counts = Counter()
    confidence_counts = Counter()
    physical_counts = Counter()
    evidence_counts = Counter()
    geometry_counts = Counter()
    role_pair_counts = Counter()
    discovery_depth_counts = Counter()

    for point in topology_points:

        classification_counts[
            point.get(
                "classification",
                "unknown"
            )
        ] += 1

    for record in connections:

        confidence_counts[
            record.get(
                "confidence",
                "UNKNOWN"
            )
        ] += 1

        physical_counts[
            record.get(
                "physical",
                "UNKNOWN"
            )
        ] += 1

        evidence_counts.update(
            record.get(
                "evidence",
                []
            )
        )

        geometry_counts[
            record.get(
                "geometry",
                {}
            ).get(
                "classification",
                "unknown"
            )
        ] += 1

        role_pair_counts[
            (
                record.get(
                    "route_role",
                    "unknown"
                ),
                record.get(
                    "adjacent_role",
                    "unknown"
                )
            )
        ] += 1

    for depth in (
        connected_infrastructure.get(
            "depth_map",
            {}
        ).values()
    ):
        discovery_depth_counts[
            depth
        ] += 1

    anomalies = detect_topology_anomalies(
        topology
    )

    return {
        "node_classification": dict(
            classification_counts
        ),
        "confidence": dict(
            confidence_counts
        ),
        "physical": dict(
            physical_counts
        ),
        "evidence": dict(
            evidence_counts
        ),
        "geometry": dict(
            geometry_counts
        ),
        "role_pairs": {
            f"{route}->{adjacent}": count
            for (
                route,
                adjacent
            ), count in role_pair_counts.items()
        },
        "discovery_depth": dict(
            discovery_depth_counts
        ),
        "anomaly_count": len(
            anomalies
        ),
        "topology_points": len(
            topology_points
        ),
        "individual_connections": len(
            connections
        ),
        "route_segments": len(
            route_segments
        ),
        "connected_way_count": len(
            connected_infrastructure.get(
                "way_ids",
                set()
            )
        )
    }


# ============================================================
# Full topology analysis
# ============================================================

def analyze_topology(
    path,
    nodes,
    graph,
    edge_metadata,
    ways,
    node_way_index
):
    """
    Complete Railway Topology Engine v1.2.
    """

    # --------------------------------------------------------
    # 1. Exact Dijkstra route edges
    # --------------------------------------------------------

    route_edges = build_route_edges(
        path
    )

    # --------------------------------------------------------
    # 2. Exact OSM ways used by those edges
    # --------------------------------------------------------

    route_way_ids = build_route_way_ids(
        route_edges,
        edge_metadata
    )

    # --------------------------------------------------------
    # 3. Exact route way membership per node
    # --------------------------------------------------------

    route_way_ids_by_node = (
        build_route_way_ids_by_node(
            path,
            edge_metadata
        )
    )

    # --------------------------------------------------------
    # 4. Topology points + individual connections
    # --------------------------------------------------------

    (
        topology_points,
        connection_records
    ) = build_topology_points(
        path,
        nodes,
        graph,
        edge_metadata,
        ways,
        node_way_index
    )

    # --------------------------------------------------------
    # 5. Connected railway world
    # --------------------------------------------------------

    connected_infrastructure = (
        build_connected_infrastructure(
            route_way_ids,
            ways,
            node_way_index
        )
    )

    # --------------------------------------------------------
    # 6. Infrastructure statistics
    # --------------------------------------------------------

    infrastructure_statistics = (
        calculate_infrastructure_statistics(
            connected_infrastructure,
            ways
        )
    )

    # --------------------------------------------------------
    # 7. Route segmentation
    # --------------------------------------------------------

    route_segments = build_route_segments(
        path,
        edge_metadata
    )

    # --------------------------------------------------------
    # 8. OSM way transitions
    # --------------------------------------------------------

    way_transitions = (
        build_route_way_transitions(
            path,
            edge_metadata
        )
    )

    # --------------------------------------------------------
    # 9. Reporting-only connection groups
    # --------------------------------------------------------

    connection_groups = (
        build_connection_groups(
            connection_records
        )
    )

    # --------------------------------------------------------
    # 10. Assemble topology
    # --------------------------------------------------------

    topology = {
        "engine_version": ENGINE_VERSION,

        "path": path,

        "route_edges": route_edges,

        "route_way_ids": route_way_ids,

        "route_way_ids_by_node": (
            route_way_ids_by_node
        ),

        "topology_points": topology_points,

        "connections": connection_records,

        "connected_infrastructure": (
            connected_infrastructure
        ),

        "infrastructure_statistics": (
            infrastructure_statistics
        ),

        "route_segments": route_segments,

        "way_transitions": way_transitions,

        "connection_groups": (
            connection_groups
        )
    }

    # --------------------------------------------------------
    # 11. Final statistics
    # --------------------------------------------------------

    topology["statistics"] = (
        calculate_topology_statistics(
            topology
        )
    )

    # --------------------------------------------------------
    # 12. Final anomaly report
    # --------------------------------------------------------

    topology["anomalies"] = (
        detect_topology_anomalies(
            topology
        )
    )

    # --------------------------------------------------------
    # 13. Duplicate detection
    # --------------------------------------------------------

    topology["duplicate_connections"] = (
        detect_duplicate_connections(
            connection_records
        )
    )

    return topology


# ============================================================
# PUBLIC API
# ============================================================

def analyze_route_topology(
    path,
    nodes,
    graph,
    edge_metadata,
    ways,
    node_way_index
):
    """
    Public RouteGen API.

    DO NOT CHANGE THIS SIGNATURE.
    """

    return analyze_topology(
        path,
        nodes,
        graph,
        edge_metadata,
        ways,
        node_way_index
    )


def build_topology(
    path,
    nodes,
    graph,
    edge_metadata,
    ways,
    node_way_index
):
    return analyze_route_topology(
        path,
        nodes,
        graph,
        edge_metadata,
        ways,
        node_way_index
    )


# ============================================================
# REPORTING
# ============================================================

def print_topology_report(
    topology
):
    statistics = topology.get(
        "statistics",
        {}
    )

    route_way_ids = topology.get(
        "route_way_ids",
        set()
    )

    topology_points = topology.get(
        "topology_points",
        []
    )

    connections = topology.get(
        "connections",
        []
    )

    route_segments = topology.get(
        "route_segments",
        []
    )

    connected_infrastructure = topology.get(
        "connected_infrastructure",
        {}
    )

    way_transitions = topology.get(
        "way_transitions",
        []
    )

    infrastructure_statistics = (
        topology.get(
            "infrastructure_statistics",
            {}
        )
    )

    anomalies = topology.get(
        "anomalies",
        []
    )

    duplicate_connections = topology.get(
        "duplicate_connections",
        []
    )

    print()
    print("================================")
    print(
        f"   Railway Topology FINAL v{ENGINE_VERSION}"
    )
    print("================================")

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("Topology summary")
    print("----------------")

    print(
        f"Route way references: "
        f"{len(route_way_ids)}"
    )

    print(
        f"Topology points:      "
        f"{len(topology_points)}"
    )

    print(
        f"Individual connections: "
        f"{len(connections)}"
    )

    print(
        f"Route segments:       "
        f"{len(route_segments)}"
    )

    print(
        f"Connected railway ways: "
        f"{len(connected_infrastructure.get('way_ids', set()))}"
    )

    print(
        f"Way transitions:      "
        f"{len(way_transitions)}"
    )

    # --------------------------------------------------------
    # Classification
    # --------------------------------------------------------

    print()
    print("Node classification")
    print("-------------------")

    for classification, count in sorted(
        statistics.get(
            "node_classification",
            {}
        ).items()
    ):
        print(
            f"{classification}: {count}"
        )

    # --------------------------------------------------------
    # Confidence
    # --------------------------------------------------------

    print()
    print("Connection confidence")
    print("---------------------")

    for confidence, count in sorted(
        statistics.get(
            "confidence",
            {}
        ).items()
    ):
        print(
            f"{confidence}: {count}"
        )

    # --------------------------------------------------------
    # Physical judgement
    # --------------------------------------------------------

    print()
    print("Physical connection judgement")
    print("-----------------------------")

    for physical, count in sorted(
        statistics.get(
            "physical",
            {}
        ).items()
    ):
        print(
            f"{physical}: {count}"
        )

    # --------------------------------------------------------
    # Geometry
    # --------------------------------------------------------

    print()
    print("Connection geometry")
    print("-------------------")

    for geometry, count in sorted(
        statistics.get(
            "geometry",
            {}
        ).items()
    ):
        print(
            f"{geometry}: {count}"
        )

    # --------------------------------------------------------
    # Role pairs
    # --------------------------------------------------------

    print()
    print("Way role pairs")
    print("--------------")

    for role_pair, count in sorted(
        statistics.get(
            "role_pairs",
            {}
        ).items()
    ):
        print(
            f"{role_pair}: {count}"
        )

    # --------------------------------------------------------
    # Evidence
    # --------------------------------------------------------

    print()
    print("Evidence")
    print("--------")

    for evidence, count in sorted(
        statistics.get(
            "evidence",
            {}
        ).items()
    ):
        print(
            f"{evidence}: {count}"
        )

    # --------------------------------------------------------
    # Connected infrastructure
    # --------------------------------------------------------

    print()
    print("Connected infrastructure")
    print("------------------------")

    depth_counts = statistics.get(
        "discovery_depth",
        {}
    )

    for depth in sorted(
        depth_counts
    ):
        print(
            f"  Discovery depth {depth}: "
            f"{depth_counts[depth]}"
        )

    # --------------------------------------------------------
    # Infrastructure role statistics
    # --------------------------------------------------------

    print()
    print("Infrastructure roles")
    print("--------------------")

    for role, count in sorted(
        infrastructure_statistics.get(
            "role_counts",
            {}
        ).items()
    ):
        print(
            f"{role}: {count}"
        )

    # --------------------------------------------------------
    # Way transitions
    # --------------------------------------------------------

    print()
    print("Way transitions")
    print("---------------")

    for transition in way_transitions:

        from_ways = ", ".join(
            str(value)
            for value in transition[
                "from_ways"
            ]
        )

        to_ways = ", ".join(
            str(value)
            for value in transition[
                "to_ways"
            ]
        )

        print(
            f"  Node {transition['node_id']}: "
            f"{from_ways} -> {to_ways}"
        )

    # --------------------------------------------------------
    # Individual connections
    # --------------------------------------------------------

    print()
    print("Individual connections")
    print("----------------------")

    for record in connections:

        geometry = record.get(
            "geometry",
            {}
        )

        angle = geometry.get(
            "angle"
        )

        if angle is None:
            angle_text = "N/A"
        else:
            angle_text = (
                f"{angle:.2f}°"
            )

        evidence = ", ".join(
            record.get(
                "evidence",
                []
            )
        )

        print(
            f"  {record['connection_id']} | "
            f"Node {record['node_id']} | "
            f"Route {record['route_way_id']} | "
            f"Adjacent {record['adjacent_way_id']} | "
            f"{record['route_role']} -> "
            f"{record['adjacent_role']} | "
            f"{record['route_position']} -> "
            f"{record['adjacent_position']} | "
            f"{geometry.get('classification')} | "
            f"angle {angle_text} | "
            f"score {record['score']} | "
            f"{record['confidence']} | "
            f"physical {record['physical']} | "
            f"{evidence}"
        )

    # --------------------------------------------------------
    # Anomalies
    # --------------------------------------------------------

    print()
    print("Topology anomalies")
    print("------------------")

    if not anomalies:
        print(
            "None detected."
        )

    else:
        for anomaly in anomalies:
            print(
                f"  {anomaly}"
            )

    # --------------------------------------------------------
    # Duplicate contacts
    # --------------------------------------------------------

    print()
    print("Duplicate connection records")
    print("----------------------------")

    if not duplicate_connections:
        print(
            "None detected."
        )

    else:
        for connection_id in (
            duplicate_connections
        ):
            print(
                f"  {connection_id}"
            )

    # --------------------------------------------------------
    # Final state
    # --------------------------------------------------------

    print()
    print("================================")
    print("      Topology analysis done")
    print("================================")