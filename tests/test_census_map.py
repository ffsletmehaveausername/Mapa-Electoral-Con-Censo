import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_census_map", ROOT / "scripts" / "build_census_map.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class DirectCensusMapExportTests(unittest.TestCase):
    def test_exports_direct_estimates_and_count_ratios_by_geoid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geometry_path = root / "map" / "site" / "data" / "census" / "tiger_2012_72_bg.geojson"
            geometry_path.parent.mkdir(parents=True)
            geometry_path.write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"GEOID": "001000000001"}, "geometry": None},
                    {"type": "Feature", "properties": {"GEOID": "001000000002"}, "geometry": None},
                ],
            }), encoding="utf-8")

            source_catalog = root / "map" / "site" / "data" / "2012" / "prekey" / "catalog.json"
            source_catalog.parent.mkdir(parents=True)
            source_catalog.write_text(json.dumps({"metrics": [
                {
                    "id": "B01003_001E",
                    "label": "Total population",
                    "topic": "population_and_household_denominators",
                    "kind": "count",
                    "table": "B01003",
                    "description": "Total population",
                    "universe": "Total population",
                },
                {
                    "id": "B01003_002E",
                    "label": "Male population",
                    "topic": "population_and_household_denominators",
                    "kind": "count",
                    "table": "B01003",
                    "description": "Male population",
                    "universe": "Total population",
                },
            ]}), encoding="utf-8")

            table_path = (
                root / "data" / "census_socioeconomic" / "acs" / "2012" / "tables" / "B01003.csv"
            )
            table_path.parent.mkdir(parents=True)
            table_path.write_text(
                "GEOID,B01003_001E,B01003_002E\n"
                "001000000001,10,4\n"
                "001000000002,0,5\n",
                encoding="utf-8",
            )
            rate_path = root / "outputs" / "rate_dictionary.csv"
            rate_path.parent.mkdir(parents=True)
            rate_path.write_text(
                "id,label,topic,universe,numerator,denominator\n"
                "male_share,Male population,Population,Total population,B01003_002E,B01003_001E\n",
                encoding="utf-8",
            )

            catalog = BUILDER.export_year(2012, root)
            output_dir = root / "map" / "site" / "data" / "census" / "acs" / "2012"
            direct = json.loads((output_dir / "B01003_direct.json").read_text(encoding="utf-8"))
            derived = json.loads(
                (output_dir / "derived_rates_direct.json").read_text(encoding="utf-8")
            )
            exported_catalog = json.loads((output_dir / "catalog.json").read_text(encoding="utf-8"))

        self.assertEqual(catalog["polygons"], 2)
        self.assertEqual(exported_catalog["geography"], "block_group")
        self.assertEqual(direct["ids"], ["001000000001", "001000000002"])
        self.assertEqual(direct["values"]["B01003_002E"], [4.0, 5.0])
        self.assertEqual(derived["values"]["male_share_source"], [40.0, None])
        self.assertEqual(
            next(metric for metric in exported_catalog["metrics"]
                 if metric["id"] == "male_share_ratio")["fields"]["direct"],
            "male_share_source",
        )


if __name__ == "__main__":
    unittest.main()
