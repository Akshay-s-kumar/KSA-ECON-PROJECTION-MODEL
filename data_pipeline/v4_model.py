"""Version 4 projection engine: V3 formulas, calculated on request for any population band.

    band + percentiles -> v4_lookups ladders (filtered comparators)
                       -> S-curve -> GVA -> jobs -> Saudi/non-Saudi -> income -> population -> consumption

The formulas are the ones in v3_model.WorkbookReference.annual_results, vectorised with numpy
(10 sectors x 48 years in one pass). Nothing is precomputed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import v4_config as cfg
from v3_model import (BASE_VALUE, DEFAULT_STEEPNESS, REGION, SECTOR_COLUMN_MAP, SECTOR_INDEX,
                      SECTOR_NAMES, SECTOR_ORDER, START_YEAR, WORKBOOK_VERSION)
from v4_lookups import Band, BandError, LookupService, ReferenceData, load_tables


class RiyadhBase:
    """Riyadh inputs (same cells V3 reads), held as numpy arrays [year, sector]."""

    def __init__(self, tables: dict[str, pd.DataFrame]):
        sector_years = tables["riyadh_sector_years"]
        self.years = np.array(sorted(sector_years.year.unique()), dtype=int)
        order = list(SECTOR_ORDER)

        def grid(column: str) -> np.ndarray:
            table = sector_years.pivot(index="year", columns="sector_code", values=column)
            return table.loc[self.years, order].to_numpy(float)

        self.base_gva = grid("base_gva")
        self.productivity = grid("productivity")
        self.population_ratio = (tables["riyadh_years"].set_index("year")
                                 .loc[self.years, "population_ratio"].to_numpy(float))

        sectors = tables["riyadh_sectors"].set_index("sector_code").loc[order]
        groups = tables["riyadh_groups"].set_index("employment_group").loc[sectors.employment_group]
        self.saudi_share = groups.saudi_share.to_numpy(float)
        self.non_saudi_share = groups.non_saudi_share.to_numpy(float)
        self.saudi_income = groups.saudi_monthly_income.to_numpy(float)
        self.non_saudi_income = groups.non_saudi_monthly_income.to_numpy(float)
        self.baseline_gva = sectors.baseline_gva_percentile.to_numpy(float)
        self.baseline_gva_emp = sectors.baseline_gva_emp_percentile.to_numpy(float)

        scalars = dict(zip(tables["riyadh_scalars"].name, tables["riyadh_scalars"].value))
        self.mpc = (float(scalars["mpc_1"]), float(scalars["mpc_2"]), float(scalars["mpc_3"]))
        self.baseline_emp_pop = float(scalars["baseline_emp_pop_percentile"])


class V4Engine:
    def __init__(self, reference: ReferenceData, base: RiyadhBase):
        if list(reference.sectors) != list(SECTOR_ORDER):
            raise ValueError("Sector codes in v4_config.SECTORS must match SECTOR_ORDER in v3_model.py.")
        self.ref = reference
        self.base = base
        self.lookups = LookupService(reference)

    @classmethod
    def from_duckdb(cls, path: Path = cfg.REFERENCE_DB_PATH) -> "V4Engine":
        tables = load_tables(path)
        missing = {"riyadh_sector_years", "riyadh_years", "riyadh_sectors", "riyadh_groups",
                   "riyadh_scalars"} - set(tables)
        if missing:
            raise ValueError(f"{path.name} has no Riyadh tables {sorted(missing)}. Re-run extract_v4.py.")
        return cls(ReferenceData(tables), RiyadhBase(tables))

    # ---- metadata for the UI -----------------------------------------------------
    @property
    def years(self) -> list[int]:
        return self.base.years.tolist()

    def default_band(self) -> Band:
        return self.ref.default_shared_band()

    def comparator_counts(self, band: Band, sector_code: str | None = None) -> dict:
        comparators = self.ref.comparators(band)
        if sector_code is None:   # all sectors: report the weakest sector
            gva = min(len(v) for v in comparators["gva"].values())
            gva_emp = min(len(v) for v in comparators["gva_emp"].values())
        else:
            gva = len(comparators["gva"][sector_code])
            gva_emp = len(comparators["gva_emp"][sector_code])
        return {"gva": gva, "gva_emp": gva_emp, "emp_pop": len(comparators["emp_pop"]),
                "gva_by_sector": {k: len(v) for k, v in comparators["gva"].items()}}

    # ---- lookups -----------------------------------------------------------------
    def _lookup(self, ladders: dict, kind: str, band: Band, code: str | None, percentile: float) -> float:
        ladder = ladders[kind] if code is None else ladders[kind][code]
        key = round(float(percentile), 2)
        if key in ladder and abs(float(percentile) - key) < 1e-9:
            return ladder[key]
        # Off the 5% grid (e.g. a workbook baseline): calculate that exact percentile.
        if kind == "gva":
            return self.ref.gva_ladder(code, band, [float(percentile)])[float(percentile)]
        if kind == "gva_emp":
            return self.ref.gva_emp_ladder(code, band, [float(percentile)])[float(percentile)]
        return self.ref.emp_pop_ladder(band, [float(percentile)])[float(percentile)]

    # ---- core projection (V3 formulas) -----------------------------------------------
    def _project(self, band: Band, gva_p, gva_emp_p, emp_pop_p) -> dict[str, np.ndarray]:
        ladders = self.lookups.ladders(band)
        targets = np.array([self._lookup(ladders, "gva", band, c, p) for c, p in zip(SECTOR_ORDER, gva_p)])
        deltas = np.array([self._lookup(ladders, "gva_emp", band, c, p) for c, p in zip(SECTOR_ORDER, gva_emp_p)])
        multipliers = np.array([self._lookup(ladders, "emp_pop", band, None, p) for p in emp_pop_p])

        too_low = [SECTOR_NAMES[c] for c, t in zip(SECTOR_ORDER, targets) if not t > BASE_VALUE]
        if too_low:
            raise BandError(f"GVA share target is at or below the S-curve start value ({BASE_VALUE}) for: "
                            f"{', '.join(too_low)}. Widen the band or raise the GVA percentile.")
        if np.any(1 + deltas <= 0) or np.any(1 + multipliers <= 0):
            raise BandError("A delta of -100% or lower came out of this band. Widen the band.")

        b = self.base
        ratio = BASE_VALUE / targets
        inflection = START_YEAR + np.log((1 - ratio) / ratio) / DEFAULT_STEEPNESS
        years = b.years[:, None]
        shares = targets / (1 + np.exp(-DEFAULT_STEEPNESS * (years - inflection)))   # [year, sector]
        gva = shares * b.base_gva
        jobs = gva * 1_000_000 / b.productivity / (1 + deltas)
        saudi = jobs * b.saudi_share
        non_saudi = jobs * b.non_saudi_share
        income = (12 * jobs * b.saudi_share * b.saudi_income / 1_000_000
                  + 12 * jobs * b.non_saudi_share * b.non_saudi_income / 1_000_000)
        population = jobs / b.population_ratio[:, None] / (1 + multipliers)
        consumption = income * 1_000_000 * b.mpc[2]
        return {"targets": targets, "deltas": deltas, "multipliers": multipliers, "shares": shares,
                "gva": gva, "jobs": jobs, "saudi": saudi, "non_saudi": non_saudi,
                "income": income, "population": population, "consumption": consumption}

    # ---- single sector (same JSON shape as /api/v3/scenario) -------------------------
    def scenario(self, band: Band, sector_code: str, gva_percentile: float,
                 gva_emp_delta_percentile: float, emp_pop_delta_percentile: float) -> dict:
        if sector_code not in SECTOR_INDEX:
            raise BandError(f"Unknown sector code '{sector_code}'.")
        band = band.validate()
        i = SECTOR_INDEX[sector_code]
        gva_p = self.base.baseline_gva.copy()
        gva_emp_p = self.base.baseline_gva_emp.copy()
        gva_p[i], gva_emp_p[i] = gva_percentile, gva_emp_delta_percentile
        emp_pop_p = np.full(len(SECTOR_ORDER), emp_pop_delta_percentile)   # V3: one multiplier for all sectors
        r = self._project(band, gva_p, gva_emp_p, emp_pop_p)

        annual = []
        for y, year in enumerate(self.years):
            annual.append({
                "year": year,
                "gva_share_s_curve": float(r["shares"][y, i]),
                "gva_sar_million": float(r["gva"][y].sum()),
                "employment_saudi": float(r["saudi"][y].sum()),
                "employment_non_saudi": float(r["non_saudi"][y].sum()),
                "employment_total": float(r["jobs"][y].sum()),
                "population": float(r["population"][y].sum()),
                "income": float(r["income"][y].sum()),
                "consumption": float(r["consumption"][y].sum()),
                "sector_gva_sar_million": float(r["gva"][y, i]),
                "sector_employment_saudi": float(r["saudi"][y, i]),
                "sector_employment_non_saudi": float(r["non_saudi"][y, i]),
                "sector_employment_total": float(r["jobs"][y, i]),
                "sector_population": float(r["population"][y, i]),
                "sector_income": float(r["income"][y, i]),
                "sector_consumption": float(r["consumption"][y, i]),
            })
        return {
            "scenario": {
                "scenario_id": scenario_id(sector_code, gva_percentile, gva_emp_delta_percentile,
                                           emp_pop_delta_percentile, band),
                "workbook_version": WORKBOOK_VERSION,
                "region": REGION,
                "sector_code": sector_code,
                "sector_name": SECTOR_NAMES[sector_code],
                "workbook_column": SECTOR_COLUMN_MAP[sector_code],
                "gva_percentile": gva_percentile,
                "gva_share_result": float(r["targets"][i]),
                "gva_emp_delta_percentile": gva_emp_delta_percentile,
                "gva_emp_delta_result": float(r["deltas"][i]),
                "emp_pop_delta_percentile": emp_pop_delta_percentile,
                "emp_pop_delta_result": float(r["multipliers"][i]),
            },
            "band": band.as_dict(),
            "comparators": self.comparator_counts(band, sector_code),
            "annual_results": annual,
        }

    # ---- all sectors (same JSON shape as /api/v3/economy/scenario) --------------------
    def economy(self, band: Band, assumptions: list[dict]) -> dict:
        band = band.validate()
        by_code = {a["sector_code"]: a for a in assumptions}
        if len(by_code) != len(assumptions):
            raise BandError("Each sector must be selected only once.")
        if set(by_code) != set(SECTOR_ORDER):
            raise BandError("Provide assumptions for every sector exactly once.")

        # A sector's own outputs depend only on its own percentiles, so one pass with each
        # sector's own assumptions equals V3's "one scenario per sector, then sum".
        gva_p = [by_code[c]["gva_percentile"] for c in SECTOR_ORDER]
        gva_emp_p = [by_code[c]["gva_emp_delta_percentile"] for c in SECTOR_ORDER]
        emp_pop_p = [by_code[c]["emp_pop_delta_percentile"] for c in SECTOR_ORDER]
        r = self._project(band, gva_p, gva_emp_p, emp_pop_p)

        annual = []
        for y, year in enumerate(self.years):
            total_gva = float(r["gva"][y].sum())
            sectors = []
            for i, code in enumerate(SECTOR_ORDER):
                gva = float(r["gva"][y, i])
                sectors.append({
                    "sector_code": code,
                    "sector_name": SECTOR_NAMES[code],
                    "gva_share_s_curve": float(r["shares"][y, i]),
                    "gva_sar_million": round(gva, 2),
                    "share_percent": round(gva / total_gva * 100, 2) if total_gva else 0,
                    "employment_saudi": round(float(r["saudi"][y, i]), 2),
                    "employment_non_saudi": round(float(r["non_saudi"][y, i]), 2),
                    "employment_total": round(float(r["jobs"][y, i]), 2),
                    "population": round(float(r["population"][y, i]), 2),
                    "income": round(float(r["income"][y, i]), 2),
                    "consumption": round(float(r["consumption"][y, i]), 2),
                })
            annual.append({
                "year": year,
                "sectors_included": len(sectors),
                "total_gva_sar_million": round(total_gva, 2),
                "employment_saudi": round(float(r["saudi"][y].sum()), 2),
                "employment_non_saudi": round(float(r["non_saudi"][y].sum()), 2),
                "employment_total": round(float(r["jobs"][y].sum()), 2),
                "population": round(float(r["population"][y].sum()), 2),
                "income": round(float(r["income"][y].sum()), 2),
                "consumption": round(float(r["consumption"][y].sum()), 2),
                "sectors": sectors,
            })
        return {
            "region": REGION,
            "metric": "GVA",
            "unit": "SAR million",
            "aggregation": "Sum of sector outputs calculated from each sector's selected assumptions.",
            "band": band.as_dict(),
            "comparators": self.comparator_counts(band),
            "annual_results": annual,
        }


def scenario_id(code: str, gva: float, gva_emp: float, emp_pop: float, band: Band) -> str:
    return (f"{WORKBOOK_VERSION}_RIY_{code.replace('-', '')}"
            f"_GVA{round(gva * 100):03d}_GE{round(gva_emp * 100):03d}_EP{round(emp_pop * 100):03d}"
            f"_N3-{int(band.nuts3_min)}-{int(band.nuts3_max)}_N2-{int(band.nuts2_min)}-{int(band.nuts2_max)}")
