from routegen.station import resolve_station

from routegen.osm import (
    download_railway_data,
    count_elements
)

from routegen.graph import (
    build_railway_graph
)

from routegen.pathfinder import (
    find_route_between_stations
)

from routegen.validator import (
    validate_route,
    print_validation_report
)

from routegen.topology import (
    analyze_route_topology,
    print_topology_report
)

from routegen.tdb import (
    export_tdb
)

from routegen.teb import (
    export_teb
)

from routegen.sdb import (
    export_sdb
)


def main():

    print("================================")
    print("        RouteGen 0.1")
    print("================================")
    print()

    # ========================================================
    # Station input
    # ========================================================

    start_query = input(
        "Enter the starting station: "
    )

    end_query = input(
        "Enter the ending station: "
    )

    print()
    print("Resolving stations...")
    print()

    print(
        f"Searching for: {start_query}"
    )

    start = resolve_station(
        start_query
    )

    print(
        "✓ Starting station resolved"
    )

    print(start)
    print()

    print(
        f"Searching for: {end_query}"
    )

    end = resolve_station(
        end_query
    )

    print(
        "✓ Ending station resolved"
    )

    print(end)
    print()

    print("================================")
    print("Station resolution successful.")
    print("================================")
    print()

    # ========================================================
    # Railway data acquisition
    # ========================================================

    margin = 0.08

    south = min(
        start.latitude,
        end.latitude
    ) - margin

    west = min(
        start.longitude,
        end.longitude
    ) - margin

    north = max(
        start.latitude,
        end.latitude
    ) + margin

    east = max(
        start.longitude,
        end.longitude
    ) + margin

    print("Railway data acquisition")
    print("------------------------")

    print(
        f"South: {south}"
    )

    print(
        f"West:  {west}"
    )

    print(
        f"North: {north}"
    )

    print(
        f"East:  {east}"
    )

    print()

    data = download_railway_data(
        south,
        west,
        north,
        east
    )

    nodes_count, ways_count, relations_count = (
        count_elements(data)
    )

    print()
    print("OSM railway data summary")
    print("------------------------")

    print(
        f"Nodes:     {nodes_count}"
    )

    print(
        f"Ways:      {ways_count}"
    )

    print(
        f"Relations: {relations_count}"
    )

    print()
    print(
        "Railway data acquisition successful."
    )

    # ========================================================
    # Railway graph construction
    # ========================================================

    print()
    print(
        "Loading OSM railway data..."
    )

    (
        nodes,
        ways,
        graph,
        edge_metadata,
        node_way_index,
        edge_count
    ) = build_railway_graph()

    print()
    print("Railway graph summary")
    print("---------------------")

    print(
        f"OSM nodes indexed: {len(nodes)}"
    )

    print(
        f"Railway ways:      {len(ways)}"
    )

    print(
        f"Graph nodes:       {len(graph)}"
    )

    print(
        f"Graph edges:       {edge_count}"
    )

    print(
        f"Edge metadata:     {len(edge_metadata)}"
    )

    print(
        f"Node-way index:    {len(node_way_index)}"
    )

    print()
    print(
        "Railway graph construction successful."
    )

    # ========================================================
    # Route pathfinding
    # ========================================================

    print()
    print(
        "Searching for connected railway route..."
    )
    print()

    route_result = find_route_between_stations(
        start.latitude,
        start.longitude,
        end.latitude,
        end.longitude,
        nodes,
        graph
    )

    if route_result is None:

        raise RuntimeError(
            "No connected railway route could be "
            "found between the selected stations."
        )

    path = route_result["path"]

    print()
    print("================================")
    print("Connected railway route found.")
    print("================================")
    print()

    print("Selected railway attachment")
    print("----------------------------")

    print(
        f"Starting graph node: "
        f"{route_result['start_node']}"
    )

    print(
        f"Ending graph node:   "
        f"{route_result['end_node']}"
    )

    print()

    print("Preliminary route summary")
    print("-------------------------")

    print(
        f"Graph nodes in route: "
        f"{len(path)}"
    )

    print(
        f"Pathfinder distance:   "
        f"{route_result['distance'] / 1000:.2f} km"
    )

    print()
    print(
        "Route pathfinding successful."
    )

    # ========================================================
    # Route validation
    # ========================================================

    print()
    print(
        "Validating generated route..."
    )

    validation_report = validate_route(
        path,
        nodes,
        expected_start=(
            start.latitude,
            start.longitude
        ),
        expected_end=(
            end.latitude,
            end.longitude
        )
    )

    print_validation_report(
        validation_report
    )

    if not validation_report.get(
        "valid",
        False
    ):

        raise RuntimeError(
            "Route validation failed. "
            "Topology analysis will not proceed."
        )

    # ========================================================
    # Railway topology analysis v1.2
    # ========================================================

    print()
    print(
        "Analyzing railway topology..."
    )

    topology = analyze_route_topology(
        path,
        nodes,
        graph,
        edge_metadata,
        ways,
        node_way_index
    )

    print_topology_report(
        topology
    )

    # ========================================================
    # TrainGo Track Database export
    # ========================================================

    print()
    print(
        "Building TrainGo Track Database..."
    )

    tdb = export_tdb(
        topology,
        nodes,
        ways
    )

    print()
    print("================================")
    print("      TDB export successful.")
    print("================================")
    print()

    print(
        f"Track records:      {len(tdb['tracks'])}"
    )

    print(
        f"Topology points:    {len(tdb['topology_points'])}"
    )

    print(
        f"Connections:        {len(tdb['connections'])}"
    )

    print(
        f"Way transitions:    {len(tdb['way_transitions'])}"
    )

    print()

    # ========================================================
    # TrainGo Terrain Database export
    # ========================================================

    print()
    print(
        "Building TrainGo Terrain Database..."
    )

    teb = export_teb(
        south,
        west,
        north,
        east,
        margin=0.02,
        satellite_zoom=15
    )

    print()
    print("================================")
    print("      TEB export successful.")
    print("================================")
    print()

    print(
        f"DEM tiles:          "
        f"{len(teb['dem']['tiles'])}"
    )

    print(
        f"Satellite tiles:    "
        f"{len(teb['satellite']['tiles'])}"
    )

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

    # ========================================================
    # TrainGo Scenery Database export
    # ========================================================

    print()
    print(
        "Building TrainGo Scenery Database..."
    )

    sdb = export_sdb(
        south,
        west,
        north,
        east,
        margin=0.01
    )

    print()
    print("================================")
    print("      SDB export successful.")
    print("================================")
    print()

    sdb_statistics = sdb[
        "statistics"
    ]

    print(
        f"Scenery objects:   "
        f"{sdb_statistics['total_objects']}"
    )

    print(
        f"Buildings with height: "
        f"{sdb_statistics['buildings_with_height']}"
    )

    print(
        f"Buildings without height: "
        f"{sdb_statistics['buildings_without_height']}"
    )

    print()

    print(
        "✓ OSM scenery acquisition"
    )

    print(
        "✓ GIS building acquisition"
    )

    print(
        "✓ Building source reconciliation"
    )

    print(
        "✓ 3D scenery metadata construction"
    )

    print(
        "✓ TrainGo Scenery Database generation"
    )

    # ========================================================
    # Final stage summary
    # ========================================================

    print()
    print("================================")
    print("      RouteGen Stage Summary")
    print("================================")
    print()

    print(
        f"Start station:       {start.name}"
    )

    print(
        f"End station:         {end.name}"
    )

    print(
        f"Route nodes:         "
        f"{validation_report['node_count']}"
    )

    print(
        f"Validated distance:  "
        f"{validation_report['route_length_km']:.2f} km"
    )

    print(
        f"Largest node gap:    "
        f"{validation_report['largest_gap_m']:.2f} m"
    )

    print()

    print("✓ Station resolution")
    print("✓ OSM railway acquisition")
    print("✓ Railway graph construction")
    print("✓ Multi-candidate pathfinding")
    print("✓ Basic route validation")
    print("✓ Railway topology classification")
    print("✓ Connected-way topology inspection")
    print("✓ TrainGo Track Database generation")
    print("✓ DEM acquisition")
    print("✓ Satellite imagery acquisition")
    print("✓ TrainGo Terrain Database generation")
    print("✓ OSM scenery acquisition")
    print("✓ GIS building acquisition")
    print("✓ Building source reconciliation")
    print("✓ TrainGo Scenery Database generation")

    print()
    print(
        "RouteGen terrain and scenery generation complete."
    )


if __name__ == "__main__":
    main()