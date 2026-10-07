"""Version 4 lookups: population band -> filtered comparator regions -> percentile ladders.

Reproduces the workbook formulas:
  GVA share     : MAX over 1995..2024 of PERCENTILE(FILTER(share_year, band), p)     (NUTS3-*!BH1375:BH1394)
  GVA-emp delta : PERCENTILE(FILTER(AF, band), p), with -1 replaced by -0.99        (GVA_EMP_DELTA-3-*)
  Emp-pop delta : PERCENTILE(FILTER(AF, band), p)                                     (Emp-Pop-Delta-3!AR)
Band test is inclusive (>= min, <= max) on NUTS3 AND NUTS2 population.
Blank/text cells are left out; zeros stored by Excel are kept so results match the workbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import v4_config as cfg


class BandError(ValueError):
    """Invalid band, or no comparator region passes it."""


@dataclass(frozen=True)
class Band:
    nuts3_min: float
    nuts3_max: float
    nuts2_min: float
    nuts2_max: float

    def validate(self) -> "Band":
        values = (self.nuts3_min, self.nuts3_max, self.nuts2_min, self.nuts2_max)
        if any(not np.isfinite(v) or v < 0 for v in values):
            raise BandError("Population figures must be positive numbers.")
        if self.nuts3_min > self.nuts3_max:
            raise BandError("NUTS3 minimum is higher than NUTS3 maximum.")
        if self.nuts2_min > self.nuts2_max:
            raise BandError("NUTS2 minimum is higher than NUTS2 maximum.")
        return self


def excel_percentile(values: np.ndarray, p: float) -> float:
    """Excel PERCENTILE / PERCENTILE.INC (linear interpolation, n-1 basis)."""
    return float(np.percentile(values, p * 100, method="linear"))


def _mask(nuts3: np.ndarray, nuts2: np.ndarray, band: Band) -> np.ndarray:
    # NaN populations (text/blank in Excel) never pass, same as Excel's comparison with "".
    return ((nuts3 >= band.nuts3_min) & (nuts3 <= band.nuts3_max)
            & (nuts2 >= band.nuts2_min) & (nuts2 <= band.nuts2_max))


class ReferenceData:
    """Comparator data held in memory as numpy arrays; built once at API start."""

    def __init__(self, tables: dict[str, pd.DataFrame]):
        gva = tables["gva_comparators"]
        self.share_years = [int(c.split("_")[1]) for c in gva.columns if c.startswith("share_")]
        share_cols = [f"share_{y}" for y in self.share_years]

        self.sectors = list(cfg.SECTORS)
        self.gva, self.gva_emp = {}, {}
        for code in self.sectors:
            g = gva[gva.sector_code == code]
            self.gva[code] = (g.nuts3_id.to_numpy(), g.nuts3_pop.to_numpy(float),
                              g.nuts2_pop.to_numpy(float), g[share_cols].to_numpy(float))
            e = tables["gva_emp_comparators"]
            e = e[e.sector_code == code]
            self.gva_emp[code] = (e.nuts3_id.to_numpy(), e.nuts3_pop.to_numpy(float),
                                  e.nuts2_pop.to_numpy(float), e.value.to_numpy(float))
        p = tables["emp_pop_comparators"]
        self.emp_pop = (p.nuts3_id.to_numpy(), p.nuts3_pop.to_numpy(float),
                        p.nuts2_pop.to_numpy(float), p.value.to_numpy(float))
        self.default_bands = tables["default_bands"]
        self.checkpoints = tables["excel_checkpoints"]
        self.metadata = tables.get("metadata")

    @classmethod
    def from_duckdb(cls, path: Path = cfg.REFERENCE_DB_PATH) -> "ReferenceData":
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
        try:
            names = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            return cls({name: con.execute(f"SELECT * FROM {name}").df() for name in names})
        finally:
            con.close()

    # ---- defaults -------------------------------------------------------------
    def workbook_band(self, lookup: str, sector_code: str | None = None) -> Band:
        rows = self.default_bands[self.default_bands.lookup == lookup]
        if sector_code is not None:
            rows = rows[rows.sector_code == sector_code]
        r = rows.iloc[0]
        return Band(r.nuts3_min, r.nuts3_max, r.nuts2_min, r.nuts2_max)

    def default_shared_band(self) -> Band:
        """One shared band for the app: the GVA band from the workbook (BH1:BH4)."""
        return self.workbook_band("gva", self.sectors[0])

    # ---- comparator counts for the UI -------------------------------------------
    def comparators(self, band: Band) -> dict:
        band.validate()
        def ids(data):
            return [str(i) for i in data[0][_mask(data[1], data[2], band)]]
        return {
            "gva": {code: ids(self.gva[code]) for code in self.sectors},
            "gva_emp": {code: ids(self.gva_emp[code]) for code in self.sectors},
            "emp_pop": ids(self.emp_pop),
        }

    # ---- ladders --------------------------------------------------------------
    def gva_ladder(self, code: str, band: Band, percentiles=cfg.GVA_PERCENTILES) -> dict[float, float]:
        ids, n3, n2, shares = self.gva[code]
        selected = shares[_mask(n3, n2, band)]
        if selected.shape[0] == 0:
            raise BandError(f"No comparator region passes the band for sector {code} (GVA share).")
        if np.isnan(selected).all(axis=0).any():   # Excel would return #CALC! for that year
            raise BandError(f"Sector {code}: a share year has no data inside the band.")
        q = np.asarray(percentiles, float) * 100
        per_year = np.nanpercentile(selected, q, axis=0, method="linear")   # (percentiles, years)
        return dict(zip(percentiles, per_year.max(axis=1).tolist()))       # BH = MAX(BI:CL)

    def gva_emp_ladder(self, code: str, band: Band, percentiles=cfg.DELTA_PERCENTILES) -> dict[float, float]:
        values = self._filtered(self.gva_emp[code], band, f"sector {code} (GVA-employment delta)")
        result = np.percentile(values, np.asarray(percentiles, float) * 100, method="linear")
        result = np.where(result == -1, -0.99, result)   # workbook rule: IF(PERCENTILE(...)=-1,-0.99,...)
        return dict(zip(percentiles, result.tolist()))

    def emp_pop_ladder(self, band: Band, percentiles=cfg.DELTA_PERCENTILES) -> dict[float, float]:
        values = self._filtered(self.emp_pop, band, "employment-population delta")
        result = np.percentile(values, np.asarray(percentiles, float) * 100, method="linear")
        return dict(zip(percentiles, result.tolist()))

    @staticmethod
    def _filtered(data, band: Band, label: str) -> np.ndarray:
        _, n3, n2, values = data
        values = values[_mask(n3, n2, band)]
        values = values[~np.isnan(values)]
        if values.size == 0:
            raise BandError(f"No comparator region passes the band for {label}.")
        return values


class LookupService:
    """Cached front door for the API: the same band is only filtered once."""

    def __init__(self, reference: ReferenceData):
        self.ref = reference
        self._ladders = lru_cache(maxsize=256)(self._build)

    def ladders(self, band: Band) -> dict:
        return self._ladders(band.validate())

    def _build(self, band: Band) -> dict:
        return {
            "gva": {code: self.ref.gva_ladder(code, band) for code in self.ref.sectors},
            "gva_emp": {code: self.ref.gva_emp_ladder(code, band) for code in self.ref.sectors},
            "emp_pop": self.ref.emp_pop_ladder(band),
        }
