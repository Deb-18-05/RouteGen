import json
import os
from pathlib import Path


# ============================================================
# TrainGo Track Database
# ============================================================

TDB_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TDB_FILE = (
    ROOT
    / "output"
    / "railway.tdb"
)


# ============================================================
# Utility functions
# ============================================================

def track_id_from_way_id(way_id):
    """
    Convert an OSM railway way ID into a TrainGo track ID.
    """

    return f"T-{way_id}"


def safe_value(value, default=None):
    """
    Return a value if it exists, otherwise a default.
    """

    if value is None:
        return default

    return value


def get_node_geometry(node_id, nodes):
    """
    Convert an OSM node ID into a TrainGo geometry point.
    """

    node = nodes.get(node_id)

    if node is None:
        return {
            "node_id": node_id,
            "latitude": None,
            "longitude": None
        }

    return {
        "node_id": node_id,
        "latitude": node.get("latitude"),
        "longitude": node.get("longitude")
    }


# ============================================================
# Track records
# ============================================================

def build_track_record(
    way_id,
    way_data,
    nodes
):
    """
    Build one TrainGo track record from one OSM railway way.

    IMPORTANT:
    One OSM way remains one TrainGo track record.

    Tracks are NOT merged or flattened here.
    """

    node_ids = list(
        way_data.get(
            "nodes",
            []
        )
    )

    geometry = []

    for node_id in node_ids:
        geometry.append(
            get_node_geometry(
                node_id,
                nodes
            )
        )

    tags = dict(
        way_data.get(
            "tags",
            {}
        )
    )

    record = {
        "track_id": track_id_from_way_id(
            way_id
        ),

        "osm_way_id": way_id,

        "role": safe_value(
            way_data.get("railway"),
            "unknown"
        ),

        "service": way_data.get(
            "service"
        ),

        "usage": way_data.get(
            "usage"
        ),

        "name": way_data.get(
            "name"
        ),

        "ref": way_data.get(
            "ref"
        ),

        "gauge": way_data.get(
            "gauge"
        ),

        "electrified": way_data.get(
            "electrified"
        ),

        "voltage": way_data.get(
            "voltage"
        ),

        "frequency": way_data.get(
            "frequency"
        ),

        "node_ids": node_ids,

        "geometry": geometry,

        "tags": tags
    }

    return record


def build_tracks(
    way_ids,
    ways,
    nodes
):
    """
    Build individual TrainGo track records.

    Every supplied way ID is preserved independently.
    """

    tracks = []

    for way_id in sorted(
        way_ids,
        key=lambda value: int(value)
    ):

        way_data = ways.get(
            way_id
        )

        if way_data is None:
            continue

        track = build_track_record(
            way_id,
            way_data,
            nodes
        )

        tracks.append(
            track
        )

    return tracks


# ============================================================
# Topology connection records
# ============================================================

def build_topology_records(
    topology
):
    """
    Convert topology engine connection records into
    TrainGo TDB topology records.

    No topology decisions are made here.

    The frozen topology engine remains authoritative.
    """

    records = []

    connections = topology.get(
        "connections",
        []
    )

    for connection in connections:

        route_way_id = connection.get(
            "route_way_id"
        )

        adjacent_way_id = connection.get(
            "adjacent_way_id"
        )

        record = {
            "connection_id": connection.get(
                "connection_id"
            ),

            "node_id": connection.get(
                "node_id"
            ),

            "route_track_id": (
                track_id_from_way_id(
                    route_way_id
                )
                if route_way_id is not None
                else None
            ),

            "adjacent_track_id": (
                track_id_from_way_id(
                    adjacent_way_id
                )
                if adjacent_way_id is not None
                else None
            ),

            "route_role": connection.get(
                "route_role"
            ),

            "adjacent_role": connection.get(
                "adjacent_role"
            ),

            "route_position": connection.get(
                "route_position"
            ),

            "adjacent_position": connection.get(
                "adjacent_position"
            ),

            "geometry": connection.get(
                "geometry",
                {}
            ),

            "evidence": list(
                connection.get(
                    "evidence",
                    []
                )
            ),

            "score": connection.get(
                "score"
            ),

            "confidence": connection.get(
                "confidence"
            ),

            "physical": connection.get(
                "physical"
            )
        }

        records.append(
            record
        )

    return records


# ============================================================
# Topology point records
# ============================================================

def build_topology_points(
    topology
):
    """
    Convert topology points into TrainGo TDB records.

    Route, connected and adjacent track membership is
    preserved explicitly.
    """

    records = []

    topology_points = topology.get(
        "topology_points",
        []
    )

    for point in topology_points:

        route_way_ids = list(
            point.get(
                "route_way_ids",
                []
            )
        )

        connected_way_ids = list(
            point.get(
                "connected_way_ids",
                []
            )
        )

        adjacent_way_ids = list(
            point.get(
                "adjacent_way_ids",
                []
            )
        )

        route_track_ids = [
            track_id_from_way_id(
                way_id
            )
            for way_id in route_way_ids
        ]

        connected_track_ids = [
            track_id_from_way_id(
                way_id
            )
            for way_id in connected_way_ids
        ]

        adjacent_track_ids = [
            track_id_from_way_id(
                way_id
            )
            for way_id in adjacent_way_ids
        ]

        context = dict(
            point.get(
                "context",
                {}
            )
        )

        roles = {}

        for way_id, role in context.get(
            "roles",
            {}
        ).items():

            roles[
                track_id_from_way_id(
                    way_id
                )
            ] = role

        context["roles"] = roles

        record = {
            "node_id": point.get(
                "node_id"
            ),

            "route_track_ids": route_track_ids,

            "connected_track_ids": connected_track_ids,

            "adjacent_track_ids": adjacent_track_ids,

            "classification": point.get(
                "classification"
            ),

            "is_endpoint": point.get(
                "is_endpoint"
            ),

            "context": context,

            "connections": list(
                point.get(
                    "connections",
                    []
                )
            )
        }

        records.append(
            record
        )

    return records


# ============================================================
# Way transition records
# ============================================================

def build_way_transitions(
    topology
):
    """
    Convert frozen topology way-transition records
    into TrainGo TDB records.
    """

    records = []

    transitions = topology.get(
        "way_transitions",
        []
    )

    for transition in transitions:

        from_way_ids = list(
            transition.get(
                "from_ways",
                []
            )
        )

        to_way_ids = list(
            transition.get(
                "to_ways",
                []
            )
        )

        record = {
            "node_id": transition.get(
                "node_id"
            ),

            "from_track_ids": [
                track_id_from_way_id(
                    way_id
                )
                for way_id in from_way_ids
            ],

            "to_track_ids": [
                track_id_from_way_id(
                    way_id
                )
                for way_id in to_way_ids
            ]
        }

        records.append(
            record
        )

    return records


# ============================================================
# Infrastructure discovery
# ============================================================

def build_infrastructure_discovery(
    topology
):
    """
    Preserve the connected-infrastructure depth map
    generated by the topology engine.
    """

    infrastructure = topology.get(
        "connected_infrastructure",
        {}
    )

    depth_map = infrastructure.get(
        "depth_map",
        {}
    )

    records = []

    for way_id in sorted(
        depth_map,
        key=lambda value: int(value)
    ):

        depth = depth_map[
            way_id
        ]

        records.append(
            {
                "track_id": track_id_from_way_id(
                    way_id
                ),

                "osm_way_id": way_id,

                "depth": depth
            }
        )

    return {
        "max_depth": infrastructure.get(
            "max_depth"
        ),

        "tracks": records
    }


# ============================================================
# TDB construction
# ============================================================

def build_tdb(
    topology,
    nodes,
    ways
):
    """
    Build the complete TrainGo Track Database.

    The TDB contains:

        - every connected railway way
        - individual track geometry
        - topology points
        - topology connections
        - way transitions
        - infrastructure discovery
        - topology statistics
        - topology anomalies
        - duplicate-connection information

    Route membership itself is intentionally NOT stored
    as a flattened route/track relation.

    The topology engine remains the authority for route
    and connection interpretation.
    """

    connected_infrastructure = topology.get(
        "connected_infrastructure",
        {}
    )

    connected_way_ids = connected_infrastructure.get(
        "way_ids",
        []
    )

    tracks = build_tracks(
        connected_way_ids,
        ways,
        nodes
    )

    topology_points = build_topology_points(
        topology
    )

    connections = build_topology_records(
        topology
    )

    way_transitions = build_way_transitions(
        topology
    )

    infrastructure_discovery = (
        build_infrastructure_discovery(
            topology
        )
    )

    database = {
        "database_type": "TDB",

        "database_version": TDB_VERSION,

        "topology_engine_version": topology.get(
            "engine_version"
        ),

        "track_count": len(
            tracks
        ),

        "tracks": tracks,

        "topology_points": topology_points,

        "connections": connections,

        "way_transitions": way_transitions,

        "infrastructure_discovery": (
            infrastructure_discovery
        ),

        "statistics": topology.get(
            "statistics",
            {}
        ),

        "anomalies": topology.get(
            "anomalies",
            []
        ),

        "duplicate_connections": topology.get(
            "duplicate_connections",
            []
        )
    }

    return database


# ============================================================
# Human-readable TDB serializer
# ============================================================

def _format_scalar(
    value
):
    """
    Format a scalar value for TDB.
    """

    if value is None:
        return "NULL"

    if isinstance(
        value,
        bool
    ):
        return (
            "TRUE"
            if value
            else "FALSE"
        )

    if isinstance(
        value,
        (int, float)
    ):
        return str(
            value
        )

    text = str(
        value
    )

    text = (
        text
        .replace(
            "\\",
            "\\\\"
        )
        .replace(
            "\"",
            "\\\""
        )
        .replace(
            "\n",
            "\\n"
        )
    )

    return f"\"{text}\""


def _write_indent(
    lines,
    level,
    text
):
    """
    Append one indented TDB line.
    """

    lines.append(
        "    " * level
        + text
    )


def _write_key_value(
    lines,
    level,
    key,
    value
):
    """
    Write a TDB key/value pair.
    """

    _write_indent(
        lines,
        level,
        f"{key} = {_format_scalar(value)}"
    )


def _write_list(
    lines,
    level,
    name,
    values
):
    """
    Write a simple indexed list.
    """

    _write_indent(
        lines,
        level,
        name
    )

    _write_indent(
        lines,
        level,
        "{"
    )

    for index, value in enumerate(
        values,
        start=1
    ):

        if isinstance(value, dict):
            _write_dict(
                lines,
                level + 1,
                f"ITEM {index}",
                value
            )

        elif isinstance(value, (list, tuple)):
            _write_list(
                lines,
                level + 1,
                f"ITEM {index}",
                value
            )

        else:
            _write_indent(
                lines,
                level + 1,
                f"{index} = {_format_scalar(value)}"
            )

    _write_indent(
        lines,
        level,
        "}"
    )


def _write_dict(
    lines,
    level,
    name,
    data
):
    """
    Write a dictionary recursively.
    """

    _write_indent(
        lines,
        level,
        name
    )

    _write_indent(
        lines,
        level,
        "{"
    )

    for key in sorted(
        data,
        key=lambda value: str(value)
    ):

        value = data[
            key
        ]

        if isinstance(
            value,
            dict
        ):

            _write_dict(
                lines,
                level + 1,
                str(key),
                value
            )

        elif isinstance(
            value,
            list
        ):

            _write_list(
                lines,
                level + 1,
                str(key),
                value
            )

        else:

            _write_key_value(
                lines,
                level + 1,
                str(key),
                value
            )

    _write_indent(
        lines,
        level,
        "}"
    )


def serialize_tdb(
    database
):
    """
    Serialize a TDB database into the native
    TrainGo human-readable format.
    """

    lines = []

    lines.append(
        "TRAIN_GO_TRACK_DATABASE"
    )

    lines.append(
        "{"
    )

    _write_key_value(
        lines,
        1,
        "DATABASE_TYPE",
        database.get(
            "database_type"
        )
    )

    _write_key_value(
        lines,
        1,
        "DATABASE_VERSION",
        database.get(
            "database_version"
        )
    )

    _write_key_value(
        lines,
        1,
        "TOPOLOGY_ENGINE_VERSION",
        database.get(
            "topology_engine_version"
        )
    )

    _write_key_value(
        lines,
        1,
        "TRACK_COUNT",
        database.get(
            "track_count"
        )
    )

    # --------------------------------------------------------
    # Tracks
    # --------------------------------------------------------

    _write_indent(
        lines,
        1,
        "TRACKS"
    )

    _write_indent(
        lines,
        1,
        "{"
    )

    for track in database.get(
        "tracks",
        []
    ):

        _write_indent(
            lines,
            2,
            f"TRACK {track['track_id']}"
        )

        _write_indent(
            lines,
            2,
            "{"
        )

        _write_key_value(
            lines,
            3,
            "OSM_WAY_ID",
            track.get(
                "osm_way_id"
            )
        )

        _write_key_value(
            lines,
            3,
            "ROLE",
            track.get(
                "role"
            )
        )

        _write_key_value(
            lines,
            3,
            "SERVICE",
            track.get(
                "service"
            )
        )

        _write_key_value(
            lines,
            3,
            "USAGE",
            track.get(
                "usage"
            )
        )

        _write_key_value(
            lines,
            3,
            "NAME",
            track.get(
                "name"
            )
        )

        _write_key_value(
            lines,
            3,
            "REF",
            track.get(
                "ref"
            )
        )

        _write_key_value(
            lines,
            3,
            "GAUGE",
            track.get(
                "gauge"
            )
        )

        _write_key_value(
            lines,
            3,
            "ELECTRIFIED",
            track.get(
                "electrified"
            )
        )

        _write_key_value(
            lines,
            3,
            "VOLTAGE",
            track.get(
                "voltage"
            )
        )

        _write_key_value(
            lines,
            3,
            "FREQUENCY",
            track.get(
                "frequency"
            )
        )

        _write_list(
            lines,
            3,
            "NODE_SEQUENCE",
            track.get(
                "node_ids",
                []
            )
        )

        _write_indent(
            lines,
            3,
            "GEOMETRY"
        )

        _write_indent(
            lines,
            3,
            "{"
        )

        for index, point in enumerate(
            track.get(
                "geometry",
                []
            ),
            start=1
        ):

            _write_indent(
                lines,
                4,
                f"POINT {index}"
            )

            _write_indent(
                lines,
                4,
                "{"
            )

            _write_key_value(
                lines,
                5,
                "NODE_ID",
                point.get(
                    "node_id"
                )
            )

            _write_key_value(
                lines,
                5,
                "LATITUDE",
                point.get(
                    "latitude"
                )
            )

            _write_key_value(
                lines,
                5,
                "LONGITUDE",
                point.get(
                    "longitude"
                )
            )

            _write_indent(
                lines,
                4,
                "}"
            )

        _write_indent(
            lines,
            3,
            "}"
        )

        _write_dict(
            lines,
            3,
            "TAGS",
            track.get(
                "tags",
                {}
            )
        )

        _write_indent(
            lines,
            2,
            "}"
        )

    _write_indent(
        lines,
        1,
        "}"
    )

    # --------------------------------------------------------
    # Topology points
    # --------------------------------------------------------

    _write_indent(
        lines,
        1,
        "TOPOLOGY_POINTS"
    )

    _write_indent(
        lines,
        1,
        "{"
    )

    for index, point in enumerate(
        database.get(
            "topology_points",
            []
        ),
        start=1
    ):

        _write_indent(
            lines,
            2,
            f"POINT {index}"
        )

        _write_indent(
            lines,
            2,
            "{"
        )

        _write_key_value(
            lines,
            3,
            "NODE_ID",
            point.get(
                "node_id"
            )
        )

        _write_key_value(
            lines,
            3,
            "CLASSIFICATION",
            point.get(
                "classification"
            )
        )

        _write_key_value(
            lines,
            3,
            "IS_ENDPOINT",
            point.get(
                "is_endpoint"
            )
        )

        _write_list(
            lines,
            3,
            "ROUTE_TRACKS",
            point.get(
                "route_track_ids",
                []
            )
        )

        _write_list(
            lines,
            3,
            "CONNECTED_TRACKS",
            point.get(
                "connected_track_ids",
                []
            )
        )

        _write_list(
            lines,
            3,
            "ADJACENT_TRACKS",
            point.get(
                "adjacent_track_ids",
                []
            )
        )

        _write_dict(
            lines,
            3,
            "CONTEXT",
            point.get(
                "context",
                {}
            )
        )

        _write_list(
            lines,
            3,
            "CONNECTION_IDS",
            point.get(
                "connections",
                []
            )
        )

        _write_indent(
            lines,
            2,
            "}"
        )

    _write_indent(
        lines,
        1,
        "}"
    )

    # --------------------------------------------------------
    # Connections
    # --------------------------------------------------------

    _write_indent(
        lines,
        1,
        "CONNECTIONS"
    )

    _write_indent(
        lines,
        1,
        "{"
    )

    for connection in database.get(
        "connections",
        []
    ):

        connection_id = connection.get(
            "connection_id",
            "UNKNOWN"
        )

        _write_indent(
            lines,
            2,
            f"CONNECTION {connection_id}"
        )

        _write_indent(
            lines,
            2,
            "{"
        )

        _write_key_value(
            lines,
            3,
            "NODE_ID",
            connection.get(
                "node_id"
            )
        )

        _write_key_value(
            lines,
            3,
            "ROUTE_TRACK",
            connection.get(
                "route_track_id"
            )
        )

        _write_key_value(
            lines,
            3,
            "ADJACENT_TRACK",
            connection.get(
                "adjacent_track_id"
            )
        )

        _write_key_value(
            lines,
            3,
            "ROUTE_ROLE",
            connection.get(
                "route_role"
            )
        )

        _write_key_value(
            lines,
            3,
            "ADJACENT_ROLE",
            connection.get(
                "adjacent_role"
            )
        )

        _write_key_value(
            lines,
            3,
            "ROUTE_POSITION",
            connection.get(
                "route_position"
            )
        )

        _write_key_value(
            lines,
            3,
            "ADJACENT_POSITION",
            connection.get(
                "adjacent_position"
            )
        )

        _write_dict(
            lines,
            3,
            "GEOMETRY",
            connection.get(
                "geometry",
                {}
            )
        )

        _write_list(
            lines,
            3,
            "EVIDENCE",
            connection.get(
                "evidence",
                []
            )
        )

        _write_key_value(
            lines,
            3,
            "SCORE",
            connection.get(
                "score"
            )
        )

        _write_key_value(
            lines,
            3,
            "CONFIDENCE",
            connection.get(
                "confidence"
            )
        )

        _write_key_value(
            lines,
            3,
            "PHYSICAL",
            connection.get(
                "physical"
            )
        )

        _write_indent(
            lines,
            2,
            "}"
        )

    _write_indent(
        lines,
        1,
        "}"
    )

    # --------------------------------------------------------
    # Way transitions
    # --------------------------------------------------------

    _write_indent(
        lines,
        1,
        "WAY_TRANSITIONS"
    )

    _write_indent(
        lines,
        1,
        "{"
    )

    for index, transition in enumerate(
        database.get(
            "way_transitions",
            []
        ),
        start=1
    ):

        _write_indent(
            lines,
            2,
            f"TRANSITION {index}"
        )

        _write_indent(
            lines,
            2,
            "{"
        )

        _write_key_value(
            lines,
            3,
            "NODE_ID",
            transition.get(
                "node_id"
            )
        )

        _write_list(
            lines,
            3,
            "FROM_TRACKS",
            transition.get(
                "from_track_ids",
                []
            )
        )

        _write_list(
            lines,
            3,
            "TO_TRACKS",
            transition.get(
                "to_track_ids",
                []
            )
        )

        _write_indent(
            lines,
            2,
            "}"
        )

    _write_indent(
        lines,
        1,
        "}"
    )

    # --------------------------------------------------------
    # Infrastructure discovery
    # --------------------------------------------------------

    infrastructure = database.get(
        "infrastructure_discovery",
        {}
    )

    _write_indent(
        lines,
        1,
        "INFRASTRUCTURE_DISCOVERY"
    )

    _write_indent(
        lines,
        1,
        "{"
    )

    _write_key_value(
        lines,
        2,
        "MAX_DEPTH",
        infrastructure.get(
            "max_depth"
        )
    )

    _write_indent(
        lines,
        2,
        "TRACKS"
    )

    _write_indent(
        lines,
        2,
        "{"
    )

    for item in infrastructure.get(
        "tracks",
        []
    ):

        _write_indent(
            lines,
            3,
            f"{item['track_id']}"
        )

        _write_indent(
            lines,
            3,
            "{"
        )

        _write_key_value(
            lines,
            4,
            "OSM_WAY_ID",
            item.get(
                "osm_way_id"
            )
        )

        _write_key_value(
            lines,
            4,
            "DEPTH",
            item.get(
                "depth"
            )
        )

        _write_indent(
            lines,
            3,
            "}"
        )

    _write_indent(
        lines,
        2,
        "}"
    )

    _write_indent(
        lines,
        1,
        "}"
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    _write_dict(
        lines,
        1,
        "STATISTICS",
        database.get(
            "statistics",
            {}
        )
    )

    # --------------------------------------------------------
    # Anomalies
    # --------------------------------------------------------

    _write_list(
        lines,
        1,
        "ANOMALIES",
        database.get(
            "anomalies",
            []
        )
    )

    # --------------------------------------------------------
    # Duplicate connections
    # --------------------------------------------------------

    _write_list(
        lines,
        1,
        "DUPLICATE_CONNECTIONS",
        database.get(
            "duplicate_connections",
            []
        )
    )

    lines.append(
        "}"
    )

    lines.append(
        ""
    )

    return "\n".join(
        lines
    )


# ============================================================
# File writing
# ============================================================

def write_tdb(
    database,
    output_path=DEFAULT_TDB_FILE
):
    """
    Write a TrainGo TDB database to disk.
    """

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

    text = serialize_tdb(
        database
    )

    with open(
        temporary,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            text
        )

    os.replace(
        temporary,
        output_path
    )

    return output_path


# ============================================================
# Public export API
# ============================================================

def export_tdb(
    topology,
    nodes,
    ways,
    output_path=DEFAULT_TDB_FILE
):
    """
    Build and write the TrainGo Track Database.

    Returns the in-memory database after successful export.
    """

    database = build_tdb(
        topology,
        nodes,
        ways
    )

    write_tdb(
        database,
        output_path
    )

    return database