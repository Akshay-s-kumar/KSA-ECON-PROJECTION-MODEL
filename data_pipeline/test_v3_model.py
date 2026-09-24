import math
import unittest

from .v3_model import (
    DELTA_PERCENTILES,
    GVA_PERCENTILES,
    SECTOR_COLUMN_MAP,
    calculate_gva_share,
    calculate_inflection_point,
    excel_percentile,
)


class V3ModelTests(unittest.TestCase):
    def test_percentile_ladder_has_twenty_values_through_95_percent(self):
        self.assertEqual(
            GVA_PERCENTILES,
            tuple(index / 20 for index in range(1, 21)),
        )
        self.assertEqual(
            DELTA_PERCENTILES,
            tuple(index / 20 for index in range(21)),
        )

    def test_sector_mapping_has_ten_workbook_columns(self):
        self.assertEqual(len(SECTOR_COLUMN_MAP), 10)
        self.assertEqual(SECTOR_COLUMN_MAP["A"], "C")
        self.assertEqual(SECTOR_COLUMN_MAP["R-U"], "L")

    def test_excel_percentile_uses_linear_interpolation(self):
        self.assertEqual(excel_percentile([0, 10], 0.25), 2.5)

    def test_logistic_curve_matches_workbook_agriculture_example(self):
        target = 0.017454618464796068
        inflection = calculate_inflection_point(target)
        self.assertTrue(math.isclose(inflection, 2043.6257750316))
        self.assertTrue(
            math.isclose(
                calculate_gva_share(2050, target),
                0.01450681011939893,
                rel_tol=1e-12,
            )
        )


if __name__ == "__main__":
    unittest.main()
