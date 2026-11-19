import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
BUILDER_PATH = SCRIPTS / "build_2012.py"
SPEC = importlib.util.spec_from_file_location("build_2012", BUILDER_PATH)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ElectionSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = BUILDER.read_elections("unikey")
        cls.precincts = BUILDER.read_elections("prekey")

    def test_retains_unmapped_source_units_and_referendum_sheet(self):
        self.assertEqual(len(self.units), 1847)
        special_units = [
            target_id for target_id in self.units
            if target_id.rsplit("-", 1)[1] in {"77", "78", "79", "80"}
        ]
        self.assertEqual(len(special_units), 314)
        self.assertEqual(self.units["001-77"]["GENERAL__GOB_PNP"], 371)
        self.assertEqual(
            self.units["001-1"]["REFERENDUM__LEGISLATURA_SI"],
            129,
        )

    def test_precinct_aggregation_includes_all_unit_returns(self):
        self.assertEqual(len(self.precincts), 110)
        expected = sum(
            row.get("GENERAL__GOB_PNP", 0)
            for row in self.units.values()
            if row["PREKEY"] == "001"
        )
        self.assertEqual(self.precincts["001"]["GENERAL__GOB_PNP"], expected)


class CensusMapExportTests(unittest.TestCase):
    def test_exports_2012_tiger_block_group_outlines(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILDER, "ROOT", root):
                BUILDER.export_tiger_2012()
            path = root / "map" / "site" / "data" / "census" / "tiger_2012_72_bg.geojson"
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["type"], "FeatureCollection")
        self.assertEqual(len(document["features"]), 2594)
        first = document["features"][0]
        self.assertEqual(first["properties"]["vintage"], 2012)
        self.assertEqual(len(first["properties"]["GEOID"]), 12)
        bounds = BUILDER.shape(first["geometry"]).bounds
        self.assertTrue(-68 <= bounds[0] <= -65)
        self.assertTrue(17 <= bounds[1] <= 19)

    def test_exports_catalog_and_method_payloads_for_map_polygons(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            targets = [{"TARGET_ID": "001"}, {"TARGET_ID": "002"}]
            full_rows = [
                {
                    "TARGET_ID": "001",
                    "B01003_001E_sum": "10",
                    "B01003_001E_total": "8",
                    "below_poverty_pct_from_counts_sum": "20",
                    "below_poverty_pct_from_counts_total": "10",
                    "below_poverty_pct_intensive": "15",
                },
                {
                    "TARGET_ID": "002",
                    "B01003_001E_sum": "",
                    "B01003_001E_total": "",
                    "below_poverty_pct_from_counts_sum": "",
                    "below_poverty_pct_from_counts_total": "",
                    "below_poverty_pct_intensive": "",
                },
            ]
            metadata = [{
                "variable": "B01003_001E",
                "table": "B01003",
                "label": "Estimate!!Total",
                "topic": "population_and_household_denominators",
                "description": "Total Population",
                "universe": "Total population",
            }]
            catalog_row = {
                "polygon_rows": 2,
                "election_rows": 2,
                "no_polygon_rows": 0,
            }
            target_files = {
                "prekey": root / "map" / "site" / "data" / "2012" / "prekey" / "geometry.geojson",
                "unikey": root / "map" / "site" / "data" / "2012" / "unikey" / "geometry.geojson",
            }
            rates = BUILDER.read_csv(ROOT / "outputs" / "rate_dictionary.csv")
            with patch.object(BUILDER, "ROOT", root), patch.object(
                BUILDER, "TARGET_FILES", target_files
            ):
                count = BUILDER.export_map_assets(
                    "prekey",
                    targets,
                    full_rows,
                    metadata,
                    {"B01003_001E": "count"},
                    catalog_row,
                    rates,
                )
                map_dir = root / "map" / "site" / "data" / "2012" / "prekey"
                catalog = json.loads((map_dir / "catalog.json").read_text(encoding="utf-8"))
                payload = json.loads(
                    (map_dir / "derived_rates_total.json").read_text(encoding="utf-8")
                )

        self.assertEqual(count, len(catalog["metrics"]))
        self.assertEqual(catalog["polygons"], 2)
        ratio = next(metric for metric in catalog["metrics"] if metric["id"] == "below_poverty_pct_ratio")
        self.assertEqual(ratio["coverage"]["total"], 1)
        self.assertEqual(payload["ids"], ["001", "002"])
        self.assertEqual(payload["values"]["below_poverty_pct_from_counts_total"], [10.0, None])


if __name__ == "__main__":
    unittest.main()
