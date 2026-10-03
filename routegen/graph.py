import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_FILE = ROOT / "data" / "osm_raw.json"


def load_osm_data():
    """
    Load raw OSM railway data.
    """

    if not RAW_DATA_FILE.exists():
        raise FileNotFoundError(
            f"Raw OSM data not found:\n{RAW_DATA_FILE}"
        )

    print("Loading OSM railway data...")

    with open(
        RAW_DATA_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    print("✓ OSM data loaded")

    return data


def extract_nodes(data):
    """
    Extract OSM nodes with coordinates.
    """

    nodes = {}

    for element in data.get("elements", []):

        if element.get("type") != "node":
            continue

        if "lat" not in element:
            continue

        if "lon" not in element:
            continue

        node_id = element["id"]

        nodes[node_id] = {
            "latitude": element["lat"],
            "longitude": element["lon"],
            "tags": element.get("tags", {})
        }

    return nodes


def extract_railway_ways(data):
    """
    Extract railway ways while preserving
    OSM metadata.

    Returns:

        {
            way_id: {
                "nodes": [...],
                "tags": {...}
            }
        }
    """

    ways = {}

    for element in data.get("elements", []):

        if element.get("type") != "way":
            continue

        tags = element.get("tags", {})

        if "railway" not in tags:
            continue

        node_sequence = element.get(
            "nodes",
            []
        )

        if len(node_sequence) < 2:
            continue

        ways[element["id"]] = {

            "nodes": node_sequence,

            "tags": tags,

            "railway": tags.get(
                "railway"
            ),

            "service": tags.get(
                "service"
            ),

            "usage": tags.get(
                "usage"
            ),

            "name": tags.get(
                "name"
            ),

            "ref": tags.get(
                "ref"
            ),

            "gauge": tags.get(
                "gauge"
            ),

            "electrified": tags.get(
                "electrified"
            ),

            "voltage": tags.get(
                "voltage"
            ),

            "frequency": tags.get(
                "frequency"
            )
        }

    return ways


def add_edge(
    graph,
    edge_metadata,
    node_a,
    node_b,
    way_id
):
    """
    Add an undirected edge to the railway graph.

    Also records which OSM way produced
    the connection.
    """

    if node_a == node_b:
        return False

    if node_a not in graph:
        graph[node_a] = set()

    if node_b not in graph:
        graph[node_b] = set()

    edge_key = tuple(
        sorted(
            (node_a, node_b)
        )
    )

    is_new_edge = (
        node_b not in graph[node_a]
    )

    graph[node_a].add(node_b)
    graph[node_b].add(node_a)

    if edge_key not in edge_metadata:

        edge_metadata[edge_key] = {
            "ways": set()
        }

    edge_metadata[edge_key][
        "ways"
    ].add(way_id)

    return is_new_edge


def build_graph(ways):
    """
    Build a topology-aware railway graph.

    Returns:

        graph

            {
                node_id: {
                    neighbour_node_id,
                    ...
                }
            }

        edge_metadata

            {
                (node_a, node_b): {
                    "ways": {
                        way_id,
                        ...
                    }
                }
            }

        edge_count
    """

    graph = {}

    edge_metadata = {}

    edge_count = 0

    for way_id, way_data in ways.items():

        node_sequence = way_data["nodes"]

        for index in range(
            len(node_sequence) - 1
        ):

            node_a = node_sequence[index]

            node_b = node_sequence[
                index + 1
            ]

            is_new_edge = add_edge(
                graph,
                edge_metadata,
                node_a,
                node_b,
                way_id
            )

            if is_new_edge:

                edge_count += 1

    return (
        graph,
        edge_metadata,
        edge_count
    )


def build_node_way_index(ways):
    """
    Build an index showing which OSM railway
    ways contain each node.

    Returns:

        {
            node_id: {
                way_id,
                way_id,
                ...
            }
        }
    """

    node_way_index = {}

    for way_id, way_data in ways.items():

        for node_id in way_data["nodes"]:

            if node_id not in node_way_index:

                node_way_index[
                    node_id
                ] = set()

            node_way_index[
                node_id
            ].add(way_id)

    return node_way_index


def build_railway_graph():
    """
    Complete topology-aware railway graph
    construction pipeline.

    Returns:

        nodes
        ways
        graph
        edge_metadata
        node_way_index
        edge_count
    """

    data = load_osm_data()

    print("Indexing OSM nodes...")

    nodes = extract_nodes(data)

    print(
        f"✓ Nodes indexed: {len(nodes)}"
    )

    print(
        "Extracting railway ways..."
    )

    ways = extract_railway_ways(data)

    print(
        f"✓ Railway ways processed: "
        f"{len(ways)}"
    )

    print(
        "Building topology-aware "
        "railway graph..."
    )

    (
        graph,
        edge_metadata,
        edge_count
    ) = build_graph(ways)

    print(
        "✓ Railway graph constructed"
    )

    print(
        "Indexing node-to-way relationships..."
    )

    node_way_index = build_node_way_index(
        ways
    )

    print(
        "✓ Node-to-way index constructed"
    )

    return (
        nodes,
        ways,
        graph,
        edge_metadata,
        node_way_index,
        edge_count
    )