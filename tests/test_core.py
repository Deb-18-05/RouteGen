import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from routegen.pathfinder import (
    _shortest_paths_to_targets,
    find_route_between_stations,
    haversine_distance,
    shortest_path,
)
from routegen.sdb import haversine_meters
from routegen.stationdata import (
    haversine_array,
    haversine_m,
    station_cache_path,
)
from routegen.tdb import serialize_tdb, write_tdb
from routegen.teb import serialize_teb
from routegen.topology import haversine_distance as topology_distance
from routegen.validator import haversine_distance as validator_distance


class HaversineTests(unittest.TestCase):
    antipodal_coordinates = (
        -84.65526689833086,
        67.07812329583612,
        84.65526689833118,
        247.0781232958357,
    )

    def test_all_distance_helpers_handle_rounding_above_one(self):
        expected_distance = math.pi * 6371000.0

        for distance in (
            haversine_distance,
            validator_distance,
            haversine_m,
            haversine_meters,
            topology_distance,
        ):
            with self.subTest(distance=distance.__module__):
                self.assertAlmostEqual(
                    distance(*self.antipodal_coordinates),
                    expected_distance,
                    places=5,
                )

        array_distance = haversine_array(
            self.antipodal_coordinates[0],
            self.antipodal_coordinates[1],
            self.antipodal_coordinates[2],
            self.antipodal_coordinates[3],
        )
        self.assertAlmostEqual(array_distance, expected_distance, places=5)


class PathfindingTests(unittest.TestCase):
    def test_shortest_path_and_multi_target_search(self):
        nodes = {
            1: {"latitude": 0.0, "longitude": 0.0},
            2: {"latitude": 0.0, "longitude": 0.0001},
            3: {"latitude": 0.0, "longitude": 0.01},
            4: {"latitude": 0.0, "longitude": 0.0101},
            5: {"latitude": 0.0, "longitude": 0.02},
            6: {"latitude": 0.0, "longitude": 0.0201},
        }
        graph = {
            1: {3},
            3: {1, 5},
            5: {3},
            2: {4},
            4: {2, 6},
            6: {4},
        }

        path, distance = shortest_path(1, 5, nodes, graph)
        self.assertEqual(path, [1, 3, 5])
        self.assertGreater(distance, 0)

        with patch(
            "routegen.pathfinder._shortest_paths_to_targets",
            wraps=_shortest_paths_to_targets,
        ) as search:
            result = find_route_between_stations(
                0.0,
                0.0,
                0.0,
                0.02,
                nodes,
                graph,
                radius_m=100.0,
                max_candidates=2,
            )

        self.assertEqual(search.call_count, 2)
        self.assertIn(result["path"], ([1, 3, 5], [2, 4, 6]))


class StationCacheTests(unittest.TestCase):
    def test_cache_names_preserve_query_precision(self):
        first = station_cache_path(1.000001, 2.0, 3.0, 4.0)
        second = station_cache_path(1.000002, 2.0, 3.0, 4.0)
        self.assertNotEqual(first, second)


class TdbSerializationTests(unittest.TestCase):
    def test_nested_anomalies_are_written_as_structured_blocks(self):
        text = serialize_tdb({
            "database_type": "TDB",
            "database_version": "1.0",
            "track_count": 0,
            "anomalies": [
                {
                    "type": "high_way_fanout",
                    "node_id": 42,
                    "way_count": 8,
                }
            ],
        })

        self.assertIn(
            "ITEM 1\n        {\n            node_id = 42",
            text,
        )
        self.assertIn('type = "high_way_fanout"', text)
        self.assertNotIn("{'type':", text)

    def test_failed_replace_preserves_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "route.tdb"
            output_path.write_text("previous database", encoding="utf-8")

            with patch(
                "routegen.tdb.os.replace",
                side_effect=OSError("simulated replacement failure"),
            ):
                with self.assertRaises(OSError):
                    write_tdb(
                        {
                            "database_type": "TDB",
                            "database_version": "1.0",
                            "track_count": 0,
                        },
                        output_path,
                    )

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "previous database",
            )


class TebSerializationTests(unittest.TestCase):
    def test_paths_are_escaped_and_failed_replace_preserves_database(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "terrain.teb"
            output_path.write_text("previous database", encoding="utf-8")

            teb = {
                "database_type": "TEB",
                "database_version": "1.0",
                "coverage": {
                    "south": 0,
                    "west": 0,
                    "north": 1,
                    "east": 1,
                },
                "dem": {
                    "source": "test",
                    "format": "HGT",
                    "statistics": {},
                    "tiles": [{
                        "name": "N00E000",
                        "latitude": 0,
                        "longitude": 0,
                        "samples": 1,
                        "file": r"output\terrain\dem\N00E000.hgt",
                    }],
                },
                "satellite": {
                    "source": "test",
                    "format": "JPEG tiles",
                    "zoom": 1,
                    "tiles": [],
                },
            }

            with patch(
                "routegen.teb.os.replace",
                side_effect=OSError("simulated replacement failure"),
            ):
                with self.assertRaises(OSError):
                    serialize_teb(teb, output_path)

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "previous database",
            )

            temporary_path = output_path.with_suffix(".teb.tmp")
            serialized = temporary_path.read_text(encoding="utf-8")
            self.assertIn(r'FILE = "output\\terrain\\dem\\N00E000.hgt"', serialized)


if __name__ == "__main__":
    unittest.main()
