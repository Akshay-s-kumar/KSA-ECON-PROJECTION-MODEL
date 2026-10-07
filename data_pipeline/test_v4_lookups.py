"""Version 4 tests.

Part 1 (always runs): formula behaviour on small synthetic data.
Part 2 (needs v4_reference.duckdb): Python must reproduce Excel's saved values
        using each lookup's own workbook band.

    python -m unittest test_v4_lookups -v
"""

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import v4_config as cfg
from v4_lookups import Band, BandError, ReferenceData, excel_percentile

TOLERANCE = 1e-12


def synthetic_tables() -> dict:
    gva_rows, emp_rows = [], []
    for code in cfg.SECTORS:
        for i, (n3, n2) in enumerate([(250_000, 8e6), (250_000, 13e6), (150_000, 8e6), (np.nan, 8e6), (300_000, 7.5e6)]):
            gva_rows.append({"sector_code": code, "excel_row": i + 2, "nuts3_id": f"R{i}",
                             "nuts3_pop": n3, "nuts2_pop": n2,
                             "share_2023": 0.01 * (i + 1), "share_2024": 0.02 * (i + 1)})
            emp_rows.append({"sector_code": code, "excel_row": i + 3, "nuts3_id": f"R{i}",
                             "nuts3_pop": n3, "nuts2_pop": n2, "value": [-1.0, 0.2, 0.3, 0.4, -1.0][i]})
    emp_pop = pd.DataFrame({"excel_row": range(2, 6), "nuts3_id": ["R0", "R1", "R2", "R3"],
                            "nuts3_pop": [250_000, 260_000, 270_000, 280_000],
                            "nuts2_pop": [8e6, 8e6, 8e6, 8e6], "value": [0.1, np.nan, 0.3, 0.5]})
    bands = pd.DataFrame([{"lookup": "gva", "sector_code": "A", "nuts3_min": 200_000, "nuts3_max": 300_000,
                           "nuts2_min": 7.5e6, "nuts2_max": 12.5e6}])
    return {"gva_comparators": pd.DataFrame(gva_rows), "gva_emp_comparators": pd.DataFrame(emp_rows),
            "emp_pop_comparators": emp_pop, "default_bands": bands,
            "excel_checkpoints": pd.DataFrame(columns=["lookup", "sector_code", "percentile", "excel_value"])}


class FormulaBehaviour(unittest.TestCase):
    def setUp(self):
        self.ref = ReferenceData(synthetic_tables())
        self.band = Band(200_000, 300_000, 7.5e6, 12.5e6)

    def test_excel_percentile_matches_percentile_inc(self):
        self.assertAlmostEqual(excel_percentile(np.array([1, 2, 3, 4]), 0.25), 1.75)

    def test_band_is_inclusive_and_needs_both_nuts_levels(self):
        passing = self.ref.comparators(self.band)["gva"]["A"]
        self.assertEqual(passing, ["R0", "R4"])   # R1 fails NUTS2, R2 fails NUTS3, R3 has no population

    def test_gva_is_max_across_years(self):
        ladder = self.ref.gva_ladder("A", self.band, [1.0])
        self.assertAlmostEqual(ladder[1.0], 0.10)  # max(2023: 0.05, 2024: 0.10)

    def test_gva_emp_minus_one_rule(self):
        ladder = self.ref.gva_emp_ladder("A", self.band, [0.0])
        self.assertEqual(ladder[0.0], -0.99)

    def test_blank_values_are_left_out(self):
        ladder = self.ref.emp_pop_ladder(self.band, [0.5])
        self.assertAlmostEqual(ladder[0.5], 0.3)   # median of 0.1, 0.3, 0.5 (blank R1 dropped)

    def test_empty_and_invalid_bands(self):
        with self.assertRaises(BandError):
            self.ref.gva_ladder("A", Band(1, 2, 1, 2))
        with self.assertRaises(BandError):
            Band(300_000, 200_000, 1, 2).validate()


@unittest.skipUnless(Path(cfg.REFERENCE_DB_PATH).exists(), "run extract_v4.py first")
class MatchesExcel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = ReferenceData.from_duckdb()

    def test_gva_targets_match_bh1375_to_bh1394(self):
        checks = self.ref.checkpoints[self.ref.checkpoints.lookup == "gva"]
        for code in self.ref.sectors:
            band = self.ref.workbook_band("gva", code)
            rows = checks[checks.sector_code == code]
            ladder = self.ref.gva_ladder(code, band, list(rows.percentile))
            for p, expected in zip(rows.percentile, rows.excel_value):
                with self.subTest(sector=code, percentile=p):
                    self.assertAlmostEqual(ladder[p], expected, delta=TOLERANCE)

    def test_emp_pop_matches_column_ar(self):
        rows = self.ref.checkpoints[self.ref.checkpoints.lookup == "emp_pop"]
        ladder = self.ref.emp_pop_ladder(self.ref.workbook_band("emp_pop"), list(rows.percentile))
        for p, expected in zip(rows.percentile, rows.excel_value):
            with self.subTest(percentile=p):
                self.assertAlmostEqual(ladder[p], expected, delta=TOLERANCE)

    def test_report_comparator_counts(self):
        band = self.ref.default_shared_band()
        counts = {k: (len(v) if isinstance(v, list) else {c: len(i) for c, i in v.items()})
                  for k, v in self.ref.comparators(band).items()}
        print(f"\nShared band {band}: {counts}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
