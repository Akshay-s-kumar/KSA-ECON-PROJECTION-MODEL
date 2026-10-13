"""Version 4 engine tests.

1. Equivalence: with the same lookup values, V4 must give exactly the V3 results
   (v3_model.WorkbookReference.annual_results) for every field, year and sector.
2. Real data (needs v4_reference.duckdb): the default band runs, or fails with a clear message.

    python -m unittest test_v4_model -v
"""

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import v4_config as cfg
import v3_model
from v3_model import SECTOR_ORDER
from v4_lookups import Band, BandError
from v4_model import RiyadhBase, V4Engine

YEARS = list(range(2023, 2071))
GROUPS = ["Skilled Sectors", "Mixed Sectors", "Labour-Intensive sectors"]
FIELDS = ["gva_share_s_curve", "gva_sar_million", "employment_saudi", "employment_non_saudi",
          "employment_total", "population", "income", "consumption", "sector_gva_sar_million",
          "sector_employment_saudi", "sector_employment_non_saudi", "sector_employment_total",
          "sector_population", "sector_income", "sector_consumption"]


def build_pair():
    """A V3 WorkbookReference and a V4 engine fed with identical synthetic inputs."""
    rng = np.random.default_rng(7)
    v3 = object.__new__(v3_model.WorkbookReference)
    v3.gva_lookup = {c: {p: 0.005 + 0.04 * p + 0.001 * i for p in v3_model.GVA_PERCENTILES}
                     for i, c in enumerate(SECTOR_ORDER)}
    v3.gva_emp_lookup = {c: {p: -0.5 + p for p in v3_model.DELTA_PERCENTILES} for c in SECTOR_ORDER}
    v3.emp_pop_lookup = {p: -0.3 + 0.8 * p for p in v3_model.DELTA_PERCENTILES}
    v3.baseline_gva_percentiles = tuple(rng.choice(v3_model.GVA_PERCENTILES, 10))
    v3.baseline_gva_emp_percentiles = tuple(rng.choice(v3_model.DELTA_PERCENTILES, 10))
    v3.baseline_emp_pop_percentile = 0.5
    v3.base_gva = {y: tuple(rng.uniform(1e4, 9e4, 10)) for y in YEARS}
    v3.productivity = {y: tuple(rng.uniform(100, 900, 10)) for y in YEARS}
    v3.population_ratio = {y: float(rng.uniform(0.3, 0.6)) for y in YEARS}
    v3.workforce_share = {g: (0.3 + 0.1 * k, 0.7 - 0.1 * k) for k, g in enumerate(GROUPS)}
    v3.income_rate = {g: (9000 + 1000 * k, 4000 + 500 * k) for k, g in enumerate(GROUPS)}
    v3.mpc = (0.6, 0.7, 0.8)
    v3.employment_groups = tuple(GROUPS[i % 3] for i in range(10))

    tables = {
        "riyadh_sector_years": pd.DataFrame([{"year": y, "sector_code": c, "base_gva": v3.base_gva[y][i],
                                               "productivity": v3.productivity[y][i]}
                                              for y in YEARS for i, c in enumerate(SECTOR_ORDER)]),
        "riyadh_years": pd.DataFrame({"year": YEARS, "population_ratio": [v3.population_ratio[y] for y in YEARS]}),
        "riyadh_sectors": pd.DataFrame({"sector_code": SECTOR_ORDER, "employment_group": v3.employment_groups,
                                        "baseline_gva_percentile": v3.baseline_gva_percentiles,
                                        "baseline_gva_emp_percentile": v3.baseline_gva_emp_percentiles}),
        "riyadh_groups": pd.DataFrame([{"employment_group": g, "saudi_share": v3.workforce_share[g][0],
                                        "non_saudi_share": v3.workforce_share[g][1],
                                        "saudi_monthly_income": v3.income_rate[g][0],
                                        "non_saudi_monthly_income": v3.income_rate[g][1]} for g in GROUPS]),
        "riyadh_scalars": pd.DataFrame({"name": ["mpc_1", "mpc_2", "mpc_3", "baseline_emp_pop_percentile"],
                                        "value": [*v3.mpc, 0.5]}),
    }

    class FixedLookups:   # V4 engine reads V3's lookup values instead of filtering
        def ladders(self, band):
            return {"gva": v3.gva_lookup, "gva_emp": v3.gva_emp_lookup, "emp_pop": v3.emp_pop_lookup}

    class FakeReference:
        sectors = list(SECTOR_ORDER)
        def comparators(self, band):
            return {"gva": {c: ["X"] for c in SECTOR_ORDER}, "gva_emp": {c: ["X"] for c in SECTOR_ORDER},
                    "emp_pop": ["X"]}

    engine = V4Engine(FakeReference(), RiyadhBase(tables))
    engine.lookups = FixedLookups()
    return v3, engine


class MatchesVersion3(unittest.TestCase):
    band = Band(1, 2, 1, 2)

    def setUp(self):
        self.v3, self.engine = build_pair()

    def test_single_sector_matches_v3_everywhere(self):
        for code in SECTOR_ORDER:
            for g, ge, ep in [(0.05, 0.0, 0.0), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.55, 0.25, 0.8)]:
                expected = self.v3.annual_results(self.v3.scenario(code, g, ge, ep), YEARS)
                actual = self.engine.scenario(self.band, code, g, ge, ep)["annual_results"]
                for e, a in zip(expected, actual):
                    for field in FIELDS:
                        with self.subTest(sector=code, inputs=(g, ge, ep), year=e.year, field=field):
                            self.assertAlmostEqual(a[field], getattr(e, field), delta=abs(getattr(e, field)) * 1e-12 + 1e-9)

    def test_all_sectors_equals_sum_of_v3_sector_scenarios(self):
        rng = np.random.default_rng(1)
        assumptions = [{"sector_code": c, "gva_percentile": float(rng.choice(v3_model.GVA_PERCENTILES)),
                        "gva_emp_delta_percentile": float(rng.choice(v3_model.DELTA_PERCENTILES)),
                        "emp_pop_delta_percentile": float(rng.choice(v3_model.DELTA_PERCENTILES))}
                       for c in SECTOR_ORDER]
        economy = self.engine.economy(self.band, assumptions)["annual_results"]
        per_sector = [self.v3.annual_results(self.v3.scenario(a["sector_code"], a["gva_percentile"],
                                                              a["gva_emp_delta_percentile"],
                                                              a["emp_pop_delta_percentile"]), YEARS)
                      for a in assumptions]
        for y, row in enumerate(economy):
            self.assertAlmostEqual(row["total_gva_sar_million"],
                                   sum(s[y].sector_gva_sar_million for s in per_sector), delta=0.02)
            self.assertAlmostEqual(row["population"], sum(s[y].sector_population for s in per_sector), delta=0.02)
            for i, sector in enumerate(row["sectors"]):
                self.assertAlmostEqual(sector["employment_total"], per_sector[i][y].sector_employment_total, delta=0.01)

    def test_target_below_s_curve_start_is_a_clear_error(self):
        self.v3.gva_lookup["A"][0.05] = 0.0
        with self.assertRaises(BandError):
            self.engine.scenario(self.band, "A", 0.05, 0.5, 0.5)


@unittest.skipUnless(Path(cfg.REFERENCE_DB_PATH).exists(), "run extract_v4.py first")
class RealWorkbook(unittest.TestCase):
    def test_default_band_runs(self):
        engine = V4Engine.from_duckdb()
        band = engine.default_band()
        try:
            result = engine.scenario(band, "A", 0.5, 0.5, 0.5)
            print(f"\nDefault band {band.as_dict()} -> comparators {result['comparators']}")
        except BandError as error:
            self.fail(f"Default band {band.as_dict()} cannot run: {error}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
