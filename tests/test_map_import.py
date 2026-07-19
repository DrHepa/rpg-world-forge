from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from worldforge.map_import import MapImportError, import_ldtk, import_map_file, import_tiled


class MapImportTests(unittest.TestCase):
    def test_imports_finite_tiled_json_layer(self) -> None:
        raw = {
            "type": "map",
            "width": 3,
            "height": 2,
            "infinite": False,
            "layers": [
                {
                    "type": "tilelayer",
                    "name": "Ground",
                    "width": 3,
                    "height": 2,
                    "data": [1, 1, 2, 1, 2, 1],
                }
            ],
        }
        result = import_tiled(
            raw,
            map_id="imported_map",
            display_name="Imported map",
            mapping={1: "ground", 2: "rock"},
            layer_name="Ground",
        )
        self.assertEqual((3, 2), (result["width"], result["height"]))
        self.assertEqual(["..#", ".#."], result["rows"])
        self.assertEqual("tiled-json", result["import"]["source_format"])

    def test_tiled_flip_flags_do_not_change_gid_mapping(self) -> None:
        raw = {
            "width": 1,
            "height": 1,
            "layers": [
                {
                    "type": "tilelayer",
                    "name": "Ground",
                    "width": 1,
                    "height": 1,
                    "data": [0x80000001],
                }
            ],
        }
        result = import_tiled(
            raw,
            map_id="flip_map",
            display_name="Flip map",
            mapping={1: "ground"},
        )
        self.assertEqual(["."], result["rows"])

    def test_imports_embedded_ldtk_intgrid(self) -> None:
        raw = {
            "defs": {},
            "levels": [
                {
                    "identifier": "Garden",
                    "layerInstances": [
                        {
                            "__identifier": "Terrain",
                            "__cWid": 3,
                            "__cHei": 2,
                            "__gridSize": 16,
                            "intGridCsv": [0, 1, 0, 1, 1, 0],
                        }
                    ],
                }
            ],
        }
        result = import_ldtk(
            raw,
            map_id="ldtk_map",
            display_name="LDtk map",
            mapping={0: "ground", 1: "rock"},
            level_name="Garden",
            layer_name="Terrain",
        )
        self.assertEqual([".#.", "##."], result["rows"])
        self.assertEqual("ldtk-json", result["import"]["source_format"])

    def test_rejects_uncompressed_tiled_data_mismatch(self) -> None:
        raw = {
            "width": 2,
            "height": 2,
            "layers": [
                {
                    "type": "tilelayer",
                    "width": 2,
                    "height": 2,
                    "data": [1],
                }
            ],
        }
        with self.assertRaises(MapImportError):
            import_tiled(
                raw,
                map_id="broken_map",
                display_name="Broken",
                mapping={1: "ground"},
            )

    def test_file_import_records_source_and_mapping_hashes(self) -> None:
        raw = {
            "width": 1,
            "height": 1,
            "layers": [
                {
                    "type": "tilelayer",
                    "width": 1,
                    "height": 1,
                    "data": [1],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            result = import_map_file(
                path,
                source_format="auto",
                map_id="hashed_map",
                display_name="Hashed map",
                mapping={1: "ground"},
            )
        self.assertEqual(64, len(result["import"]["source_sha256"]))
        self.assertEqual(64, len(result["import"]["mapping_sha256"]))


if __name__ == "__main__":
    unittest.main()
