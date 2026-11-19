import unittest
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_2012 as builder


class ApportionmentTests(unittest.TestCase):
    def test_negative_census_estimates_are_not_missing(self):
        self.assertEqual(builder.numeric("-23600"), -23600)
        self.assertTrue(np.isnan(builder.numeric("-666666666")))
        self.assertTrue(np.isnan(builder.numeric("")))
        with self.assertRaises(ValueError):
            builder.numeric("inf")

    def test_paper_weights_missing_and_uncovered(self):
        source = {"a": box(0, 0, 10, 10), "b": box(10, 0, 20, 10)}
        targets = [
            {"TARGET_ID": "1", "geometry": box(0, 0, 5, 10)},
            {"TARGET_ID": "2", "geometry": box(5, 0, 15, 10)},
            {"TARGET_ID": "3", "geometry": box(30, 0, 40, 10)},
        ]
        overlay = builder.build_intersections(targets, source)
        for mode, values, expected in (
            ("sum", [100, 200], [50, 250, np.nan]),
            ("total", [100, 200], [50, 150, np.nan]),
            ("intensive", [10, 30], [10, 20, np.nan]),
            ("sum", [0, np.nan], [0, np.nan, np.nan]),
        ):
            np.testing.assert_allclose(
                builder.interpolate(np.array(values), overlay, mode, len(targets)),
                expected, equal_nan=True,
            )

    def test_2012_labels_and_universes_use_2012_sources(self):
        variables, universes = builder.official_metadata()
        row = builder.correct_metadata(
            {"variable": "B25074_008E", "table": "B25074",
             "label": "2016 label", "description": "", "universe": ""},
            variables, universes,
        )
        self.assertEqual(row["label"], "Estimate!!Total!!Less than $10,000!!Not computed")
        self.assertEqual(row["universe"], "Renter-occupied housing units")
        with self.assertRaises(ValueError):
            builder.correct_metadata(
                {"variable": "B25074_008E", "table": "B25036"},
                variables, universes,
            )


if __name__ == "__main__":
    unittest.main()
