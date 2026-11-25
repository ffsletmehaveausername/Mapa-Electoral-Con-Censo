import csv
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_census_map", ROOT / "scripts" / "build_census_map.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class DirectCensusMapExportTests(unittest.TestCase):
    def test_income_and_earnings_counts_have_plain_labels(self):
        self.assertEqual(
            BUILDER.direct_label({"id": "B19001_002E", "table": "B19001",
                                  "label": "B19001 Total:!!Less than $10,000"}),
            "Household income: Less than $10,000 (count)",
        )
        self.assertEqual(
            BUILDER.direct_label({
                "id": "B20001_024E",
                "table": "B20001",
                "label": "B20001 Total:!!Female:!!$1 to $2,499 or loss",
            }),
            "Earnings, age 16+ with earnings: Female - $1 to $2,499 or loss (count)",
        )
        self.assertEqual(
            BUILDER.direct_label({"id": "B20002_001E", "table": "B20002", "label": "unused"}),
            "Median earnings, people with earnings age 16+",
        )

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
                {
                    "id": "B01003_003E",
                    "label": "Not selected",
                    "topic": "population_and_household_denominators",
                    "kind": "count",
                    "table": "B01003",
                    "description": "Not selected",
                    "universe": "Total population",
                },
            ]}), encoding="utf-8")

            acs_path = root / "data" / "census_socioeconomic" / "acs"
            source_path = root / "outputs" / "curated_test" / "2012" / "block_groups.csv"
            source_path.parent.mkdir(parents=True)
            (root / "outputs" / "curated_test" / "STATUS.txt").write_text(
                "COMPLETE: test output", encoding="utf-8"
            )
            (root / "outputs" / "curated_latest.json").write_text(
                json.dumps({"run": "curated_test"}), encoding="utf-8"
            )
            acs_path.mkdir(parents=True)
            (acs_path / "curated_indicators.json").write_text(json.dumps({
                "estimates": ["B01003_001E", "B01003_002E"],
                "rates": ["male_share"],
                "currency": [],
            }), encoding="utf-8")
            source_path.write_text(
                "GEOID,B01003_001E,B01003_001E_quality,B01003_002E,B01003_002E_quality,"
                "male_share,male_share_quality\n"
                "001000000001,10,lower_uncertainty,4,high_uncertainty,40,unassessable_derived\n"
                "001000000002,0,unassessable,5,moderate_uncertainty,,unassessable_derived\n",
                encoding="utf-8",
            )
            rate_path = root / "outputs" / "curated_test" / "rate_dictionary.csv"
            rate_path.parent.mkdir(parents=True, exist_ok=True)
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
        self.assertEqual(direct["quality"]["B01003_002E"],
                         ["high_uncertainty", "moderate_uncertainty"])
        self.assertEqual(derived["values"]["male_share_source"], [40.0, None])
        self.assertEqual(derived["quality"]["male_share_source"],
                         ["unassessable_derived", "unassessable_derived"])
        self.assertNotIn("B01003_003E", [metric["id"] for metric in exported_catalog["metrics"]])
        self.assertEqual(
            next(metric for metric in exported_catalog["metrics"]
                 if metric["id"] == "male_share_ratio")["fields"]["direct"],
            "male_share_source",
        )
        self.assertEqual(
            next(metric for metric in exported_catalog["metrics"]
                 if metric["id"] == "male_share_ratio")["label"],
            "Male population",
        )

    def test_published_map_matches_the_current_curated_analysis(self):
        spec = json.loads(
            (ROOT / "data" / "census_socioeconomic" / "acs" / "curated_indicators.json")
            .read_text(encoding="utf-8")
        )
        run = json.loads((ROOT / "outputs" / "curated_latest.json").read_text(
            encoding="utf-8"
        ))["run"]
        expected = set(spec["estimates"]) | {f"{rate}_ratio" for rate in spec["rates"]}
        for year in BUILDER.YEARS:
            with (ROOT / "outputs" / run / str(year) / "block_groups.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                source = {row["GEOID"]: row for row in csv.DictReader(stream)}
            directory = ROOT / "map" / "site" / "data" / "census" / "acs" / str(year)
            catalog = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual({metric["id"] for metric in catalog["metrics"]}, expected)
            for metric in catalog["metrics"]:
                payload = json.loads((directory / f"{metric['table']}_direct.json").read_text(
                    encoding="utf-8"
                ))
                field = metric["fields"]["direct"]
                source_field = field.removesuffix("_source")
                self.assertEqual(set(payload["ids"]), set(source))
                self.assertEqual(len(payload["ids"]), len(payload["values"][field]))
                self.assertEqual(len(payload["ids"]), len(payload["quality"][field]))
                for geoid, value, flag in zip(
                    payload["ids"], payload["values"][field], payload["quality"][field]
                ):
                    row = source[geoid]
                    expected_value = BUILDER.numeric(row[source_field])
                    self.assertTrue(
                        value is None and expected_value is None
                        or value is not None and expected_value is not None
                        and math.isclose(value, expected_value, rel_tol=1e-12, abs_tol=1e-12)
                    )
                    self.assertEqual(flag, row[f"{source_field}_quality"])


if __name__ == "__main__":
    unittest.main()
