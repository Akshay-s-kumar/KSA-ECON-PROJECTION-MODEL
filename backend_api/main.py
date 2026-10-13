from contextlib import asynccontextmanager
import asyncio
import sys
from pathlib import Path
from typing import Optional

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "data_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

from v4_lookups import Band, BandError
from v4_model import V4Engine
import v4_config
# ============================================================
# PATH CONFIGURATION
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
PROCESSED_DIR = PROJECT_DIR / "data_pipeline" / "processed"
FRONTEND_DIR = PROJECT_DIR / "static_frontend"

GVA_CSV_PATH = PROCESSED_DIR / "riyadh_gva_clean.csv"
ANALYTICS_CSV_PATH = PROCESSED_DIR / "riyadh_economic_projection.csv"
BOUNDARIES_PATH = PROCESSED_DIR / "saudi_boundaries.geojson"
V3_DATABASE_PATH = PROCESSED_DIR / "v3_scenarios.duckdb"
V4_REFERENCE_PATH = v4_config.REFERENCE_DB_PATH

METRICS = {
    "gva": ("GVA_Value", "Gross Value Added", "SAR million"),
    "employment": ("Employment_Value", "Employment", "thousand jobs"),
    "productivity": ("Productivity", "Productivity", "SAR thousand per job"),
    "gva_share": ("GVA_Share", "GVA Share", "percent"),
    "employment_share": ("Employment_Share", "Employment Share", "percent"),
}


# ============================================================
# STATE
# ============================================================
database_connection: Optional[duckdb.DuckDBPyConnection] = None   # V1-V3 (shared, serialised)
legacy_csv_loaded = False
v4_engine: Optional[V4Engine] = None                                # V4 (in memory, no lock needed)
v4_load_error: Optional[str] = None


# ============================================================
# REQUEST MODELS
# ============================================================
class SectorAssumptions(BaseModel):
    sector_code: str
    gva_percentile: float = Field(ge=0.05, le=1)
    gva_emp_delta_percentile: float = Field(ge=0, le=1)
    emp_pop_delta_percentile: float = Field(ge=0, le=1)


class EconomyScenarioRequest(BaseModel):
    region: str = "Riyadh"
    sectors: list[SectorAssumptions]


class BandModel(BaseModel):
    nuts3_min: float = Field(ge=0)
    nuts3_max: float = Field(ge=0)
    nuts2_min: float = Field(ge=0)
    nuts2_max: float = Field(ge=0)

    def to_band(self) -> Band:
        return Band(self.nuts3_min, self.nuts3_max, self.nuts2_min, self.nuts2_max)


class V4ScenarioRequest(BaseModel):
    sector_code: str
    region: str = "Riyadh"
    band: BandModel
    gva_percentile: float = Field(ge=0.05, le=1)
    gva_emp_delta_percentile: float = Field(ge=0, le=1)
    emp_pop_delta_percentile: float = Field(ge=0, le=1)


class V4EconomyRequest(BaseModel):
    region: str = "Riyadh"
    band: BandModel
    sectors: list[SectorAssumptions]


# ============================================================
# DUCKDB CONNECTION (V1-V3)
# ============================================================
def get_database() -> duckdb.DuckDBPyConnection:
    """Return the active DuckDB connection."""
    if database_connection is None:
        raise HTTPException(status_code=503, detail="The analytical database has not been initialized.")
    return database_connection


def get_legacy_database() -> duckdb.DuckDBPyConnection:
    """Connection for the V1/V2 CSV endpoints."""
    connection = get_database()
    if not legacy_csv_loaded:
        raise HTTPException(
            status_code=503,
            detail=f"Legacy CSV not found: {ANALYTICS_CSV_PATH.name}. Run process_data.py to enable V1/V2 endpoints.",
        )
    return connection


def initialize_database() -> duckdb.DuckDBPyConnection:
    """
    Create an in-memory DuckDB database. The V3 scenario database and the
    V1/V2 CSV are attached when present; neither is required for Version 4.
    """
    global legacy_csv_loaded
    connection = duckdb.connect(database=":memory:")

    if V3_DATABASE_PATH.exists():
        v3_path_sql = V3_DATABASE_PATH.as_posix().replace("'", "''")
        connection.execute(f"ATTACH '{v3_path_sql}' AS v3 (READ_ONLY)")

    legacy_csv_loaded = False
    if ANALYTICS_CSV_PATH.exists():
        csv_path_sql = ANALYTICS_CSV_PATH.as_posix().replace("'", "''")
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW riyadh_gva AS
            SELECT
                CAST(Region AS VARCHAR) AS Region,
                CAST(Year AS INTEGER) AS Year,
                CAST(NACE_Code AS VARCHAR) AS NACE_Code,
                CAST(Sector_Name AS VARCHAR) AS Sector_Name,
                CAST(GVA_Value AS DOUBLE) AS GVA_Value,
                CAST(Employment_Value AS DOUBLE) AS Employment_Value,
                CAST(Productivity AS DOUBLE) AS Productivity,
                CAST(GVA_Share AS DOUBLE) * 100 AS GVA_Share,
                CAST(Employment_Share AS DOUBLE) * 100 AS Employment_Share
            FROM read_csv_auto('{csv_path_sql}', header = true)
            WHERE
                Year IS NOT NULL
                AND NACE_Code IS NOT NULL
                AND Sector_Name IS NOT NULL
                AND GVA_Value IS NOT NULL
                AND Employment_Value IS NOT NULL
            """
        )
        legacy_csv_loaded = connection.execute("SELECT COUNT(*) FROM riyadh_gva").fetchone()[0] > 0
    return connection


def v3_available(connection: duckdb.DuckDBPyConnection) -> bool:
    """True only when the V3 database is actually attached (not just present on disk)."""
    return connection.execute(
        "SELECT COUNT(*) FROM duckdb_databases() WHERE database_name = 'v3'"
    ).fetchone()[0] > 0


def load_v4_engine() -> None:
    global v4_engine, v4_load_error
    v4_engine, v4_load_error = None, None
    if not V4_REFERENCE_PATH.exists():
        v4_load_error = f"{V4_REFERENCE_PATH.name} not found. Run extract_v4.py first."
        return
    try:
        v4_engine = V4Engine.from_duckdb(V4_REFERENCE_PATH)
    except Exception as error:   # report at startup, keep the API running
        v4_load_error = f"Could not load {V4_REFERENCE_PATH.name}: {error}"


def get_v4_engine() -> V4Engine:
    if v4_engine is None:
        raise HTTPException(status_code=503, detail=v4_load_error or "Version 4 engine is not loaded.")
    return v4_engine


# ============================================================
# APPLICATION LIFESPAN
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DuckDB and the Version 4 engine when the API starts."""
    global database_connection

    print("=" * 60)
    print("Starting Riyadh Economic Digital Twin API")
    print("=" * 60)

    database_connection = initialize_database()

    if legacy_csv_loaded:
        row_count, min_year, max_year = database_connection.execute(
            "SELECT COUNT(*), MIN(Year), MAX(Year) FROM riyadh_gva"
        ).fetchone()
        print(f"[SUCCESS] V1/V2 CSV loaded: {row_count} records, {min_year} to {max_year}")
    else:
        print(f"[INFO] V1/V2 CSV not found ({ANALYTICS_CSV_PATH.name}); legacy endpoints disabled")

    print(f"[{'SUCCESS' if v3_available(database_connection) else 'INFO'}] "
          f"V3 scenario database {'attached' if v3_available(database_connection) else 'not found'}")

    load_v4_engine()
    if v4_engine is not None:
        band = v4_engine.default_band()
        print(f"[SUCCESS] V4 engine loaded: {len(v4_engine.years)} years "
              f"({v4_engine.years[0]}-{v4_engine.years[-1]}), default band {band.as_dict()}")
        try:
            print(f"[INFO] Regions passing the default band: {v4_engine.comparator_counts(band)}")
        except BandError as error:
            print(f"[WARNING] Default band: {error}")
    else:
        print(f"[WARNING] {v4_load_error}")

    if BOUNDARIES_PATH.exists():
        print(f"[SUCCESS] Boundary file found: {BOUNDARIES_PATH}")
    else:
        print(f"[WARNING] Boundary file not found: {BOUNDARIES_PATH}")

    yield

    if database_connection is not None:
        database_connection.close()
        database_connection = None
    print("[SUCCESS] Riyadh Economic Digital Twin API stopped")


# ============================================================
# FASTAPI APPLICATION
# ============================================================
app = FastAPI(
    title="Riyadh Economic Digital Twin API",
    description=(
        "Local API for Riyadh economic projections. Version 4 calculates scenarios "
        "on request for any comparator population band."
    ),
    version="4.0.0",
    lifespan=lifespan,
)

request_lock = asyncio.Lock()


def metric_definition(metric: str) -> tuple[str, str, str]:
    try:
        return METRICS[metric]
    except KeyError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported metric '{metric}'. Choose one of: {', '.join(METRICS)}.",
        ) from error


@app.middleware("http")
async def serialize_database_requests(request, call_next):
    """
    Serialize only the requests that use the shared DuckDB connection (V1-V3).
    Version 4, boundaries and the static frontend run without waiting.
    """
    path = request.url.path
    uses_shared_connection = (
        path.startswith("/api/")
        and not path.startswith("/api/v4/")
        and path != "/api/boundaries"
    )
    if not uses_shared_connection:
        return await call_next(request)
    async with request_lock:
        return await call_next(request)


# ============================================================
# CORS CONFIGURATION
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ============================================================
# ROOT AND HEALTH ENDPOINTS
# ============================================================
@app.get("/", tags=["System"])
def root():
    """Serve the single-page frontend."""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health", tags=["System"])
def health_check():
    """Report which data sources are available."""
    connection = get_database()
    record_count = (
        connection.execute("SELECT COUNT(*) FROM riyadh_gva").fetchone()[0] if legacy_csv_loaded else 0
    )
    return {
        "status": "healthy",
        "database": "connected",
        "gva_csv_available": GVA_CSV_PATH.exists(),
        "analytics_csv_available": ANALYTICS_CSV_PATH.exists(),
        "boundaries_available": BOUNDARIES_PATH.exists(),
        "gva_record_count": record_count,
        "v3_database_available": v3_available(connection),
        "v4_engine_loaded": v4_engine is not None,
        "v4_message": v4_load_error,
    }


# ============================================================
# VERSION 4 ENDPOINTS (calculated on request)
# ============================================================
def check_region(region: str) -> None:
    if region.lower() != "riyadh":
        raise HTTPException(status_code=404, detail=f"Region '{region}' is not available in Version 4.")


@app.get("/api/v4/filters", tags=["Version 4"])
def get_v4_filters():
    """Selectors, year range and the default comparator band (workbook figures)."""
    engine = get_v4_engine()
    from v3_model import SECTOR_COLUMN_MAP, SECTOR_NAMES, SECTOR_ORDER

    metadata = engine.ref.metadata
    return {
        "regions": [{"code": "Riyadh", "name": "Riyadh Region"}],
        "sectors": [
            {"code": code, "name": SECTOR_NAMES[code], "workbook_column": SECTOR_COLUMN_MAP[code]}
            for code in SECTOR_ORDER
        ],
        "percentiles": {
            "gva": v4_config.GVA_PERCENTILES,
            "gva_emp_delta": v4_config.DELTA_PERCENTILES,
            "emp_pop_delta": v4_config.DELTA_PERCENTILES,
        },
        "years": {"minimum": engine.years[0], "maximum": engine.years[-1], "default": engine.years[0]},
        "default_band": engine.default_band().as_dict(),
        "workbook": None if metadata is None else metadata.iloc[0].to_dict(),
    }


@app.post("/api/v4/comparators", tags=["Version 4"])
def get_v4_comparators(band: BandModel):
    """Which comparator regions pass a band (IDs and counts)."""
    engine = get_v4_engine()
    try:
        b = band.to_band().validate()
        return {"band": b.as_dict(), "counts": engine.comparator_counts(b), "regions": engine.ref.comparators(b)}
    except BandError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v4/scenario", tags=["Version 4"])
def get_v4_scenario(payload: V4ScenarioRequest):
    """One sector projection for the chosen band and percentiles."""
    engine = get_v4_engine()
    check_region(payload.region)
    try:
        return engine.scenario(
            payload.band.to_band(),
            payload.sector_code,
            payload.gva_percentile,
            payload.gva_emp_delta_percentile,
            payload.emp_pop_delta_percentile,
        )
    except BandError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v4/economy/scenario", tags=["Version 4"])
def get_v4_economy_scenario(payload: V4EconomyRequest):
    """All sectors, each with its own assumptions, for the chosen band."""
    engine = get_v4_engine()
    check_region(payload.region)
    try:
        return engine.economy(payload.band.to_band(), [s.model_dump() for s in payload.sectors])
    except BandError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v4/reload", tags=["Version 4"])
def reload_v4_reference():
    """Reload v4_reference.duckdb after re-running extract_v4.py (no API restart needed)."""
    load_v4_engine()
    if v4_engine is None:
        raise HTTPException(status_code=503, detail=v4_load_error)
    return {"status": "reloaded", "default_band": v4_engine.default_band().as_dict()}


# ============================================================
# VERSION 3 ENDPOINTS (precomputed database, kept for comparison)
# ============================================================
@app.get("/api/v3/filters", tags=["Version 3"])
def get_v3_filters():
    """Return the workbook-approved Version 3 selectors and year range."""
    connection = get_database()
    if not v3_available(connection):
        raise HTTPException(status_code=503, detail="Version 3 scenario database has not been generated.")

    sectors = connection.execute(
        """
        SELECT sector_code, sector_name, workbook_column
        FROM v3.scenario_master
        GROUP BY sector_code, sector_name, workbook_column
        ORDER BY sector_code
        """
    ).fetchall()
    years = connection.execute(
        "SELECT MIN(year), MAX(year) FROM v3.scenario_annual_results"
    ).fetchone()

    return {
        "regions": [{"code": "Riyadh", "name": "Riyadh Region"}],
        "sectors": [{"code": row[0], "name": row[1], "workbook_column": row[2]} for row in sectors],
        "percentiles": {
            "gva": [index / 20 for index in range(1, 21)],
            "gva_emp_delta": [index / 20 for index in range(21)],
            "emp_pop_delta": [index / 20 for index in range(21)],
        },
        "years": {"minimum": years[0], "maximum": years[1], "default": years[0]},
    }


@app.get("/api/v3/economy", tags=["Version 3"])
def get_v3_economy(region: str = Query(default="Riyadh")):
    """Return annual GVA values by sector and their regional totals."""
    connection = get_legacy_database()
    records = connection.execute(
        """
        SELECT Year, NACE_Code, Sector_Name, SUM(GVA_Value) AS sector_gva_sar_million
        FROM riyadh_gva
        WHERE LOWER(Region) = LOWER(?)
        GROUP BY Year, NACE_Code, Sector_Name
        ORDER BY Year, sector_gva_sar_million DESC, NACE_Code
        """,
        [region],
    ).fetchall()
    if not records:
        raise HTTPException(status_code=404, detail=f"No economic projection data was found for region '{region}'.")

    annual_by_year = {}
    for year, sector_code, sector_name, sector_gva in records:
        annual = annual_by_year.setdefault(year, {"year": year, "sectors": [], "total_gva_sar_million": 0.0})
        value = float(sector_gva)
        annual["sectors"].append(
            {"sector_code": sector_code, "sector_name": sector_name, "gva_sar_million": round(value, 2)}
        )
        annual["total_gva_sar_million"] += value

    annual_results = []
    for annual in annual_by_year.values():
        total_gva = annual["total_gva_sar_million"]
        annual_results.append(
            {
                "year": annual["year"],
                "sectors_included": len(annual["sectors"]),
                "total_gva_sar_million": round(total_gva, 2),
                "sectors": [
                    {
                        **sector,
                        "share_percent": (
                            round(sector["gva_sar_million"] / total_gva * 100, 2) if total_gva else 0
                        ),
                    }
                    for sector in annual["sectors"]
                ],
            }
        )

    return {
        "region": region,
        "metric": "GVA",
        "unit": "SAR million",
        "aggregation": "Sum of GVA across all available sectors.",
        "annual_results": annual_results,
    }


@app.post("/api/v3/economy/scenario", tags=["Version 3"])
def get_v3_economy_scenario(payload: EconomyScenarioRequest):
    """Combine the selected precomputed assumptions for every sector."""
    connection = get_database()
    if not v3_available(connection):
        raise HTTPException(status_code=503, detail="Version 3 scenario database has not been generated.")

    available_sectors = connection.execute(
        """
        SELECT sector_code
        FROM v3.scenario_master
        WHERE LOWER(region) = LOWER(?)
        GROUP BY sector_code
        ORDER BY sector_code
        """,
        [payload.region],
    ).fetchall()
    if not available_sectors:
        raise HTTPException(status_code=404, detail=f"No Version 3 scenarios were found for region '{payload.region}'.")

    available_codes = {row[0] for row in available_sectors}
    requested_codes = [sector.sector_code for sector in payload.sectors]
    if len(requested_codes) != len(set(requested_codes)):
        raise HTTPException(status_code=422, detail="Each sector must be selected only once.")
    if set(requested_codes) != available_codes:
        raise HTTPException(status_code=422, detail="Provide assumptions for every available sector exactly once.")

    selected_scenarios = []
    for assumptions in payload.sectors:
        if (
            round(assumptions.gva_percentile, 2) not in tuple(index / 20 for index in range(1, 21))
            or round(assumptions.gva_emp_delta_percentile, 2) not in tuple(index / 20 for index in range(21))
            or round(assumptions.emp_pop_delta_percentile, 2) not in tuple(index / 20 for index in range(21))
        ):
            raise HTTPException(status_code=422, detail="Assumptions must use the available 5% percentile increments.")

        scenario = connection.execute(
            """
            SELECT scenario_id, sector_code
            FROM v3.scenario_master
            WHERE LOWER(region) = LOWER(?)
              AND sector_code = ?
              AND gva_percentile = ?
              AND gva_emp_delta_percentile = ?
              AND emp_pop_delta_percentile = ?
            """,
            [
                payload.region,
                assumptions.sector_code,
                round(assumptions.gva_percentile, 2),
                round(assumptions.gva_emp_delta_percentile, 2),
                round(assumptions.emp_pop_delta_percentile, 2),
            ],
        ).fetchone()
        if scenario is None:
            raise HTTPException(
                status_code=404,
                detail=f"A matching precomputed scenario was not found for sector '{assumptions.sector_code}'.",
            )
        selected_scenarios.append(scenario)

    placeholders = ", ".join("?" for _ in selected_scenarios)
    records = connection.execute(
        f"""
        SELECT
            master.sector_code, master.sector_name, annual.year, annual.gva_share_s_curve,
            annual.sector_gva_sar_million, annual.sector_employment_saudi,
            annual.sector_employment_non_saudi, annual.sector_employment_total,
            annual.sector_population, annual.sector_income, annual.sector_consumption
        FROM v3.scenario_master AS master
        JOIN v3.scenario_annual_results AS annual
            ON annual.scenario_id = master.scenario_id
        WHERE master.scenario_id IN ({placeholders})
        ORDER BY annual.year, master.sector_code
        """,
        [scenario[0] for scenario in selected_scenarios],
    ).fetchall()

    metrics = ("employment_saudi", "employment_non_saudi", "employment_total", "population", "income", "consumption")
    annual_by_year = {}
    for row in records:
        (sector_code, sector_name, year, gva_share, sector_gva, employment_saudi,
         employment_non_saudi, employment_total, population, income, consumption) = row
        annual = annual_by_year.setdefault(
            year, {"year": year, "sectors": [], "total_gva_sar_million": 0.0, **{m: 0.0 for m in metrics}}
        )
        sector_values = {
            "sector_code": sector_code,
            "sector_name": sector_name,
            "gva_share_s_curve": float(gva_share),
            "gva_sar_million": float(sector_gva),
            "employment_saudi": float(employment_saudi),
            "employment_non_saudi": float(employment_non_saudi),
            "employment_total": float(employment_total),
            "population": float(population),
            "income": float(income),
            "consumption": float(consumption),
        }
        annual["sectors"].append(sector_values)
        annual["total_gva_sar_million"] += float(sector_gva)
        for metric in metrics:
            annual[metric] += sector_values[metric]

    annual_results = []
    for annual in annual_by_year.values():
        total_gva = annual["total_gva_sar_million"]
        annual_results.append(
            {
                "year": annual["year"],
                "sectors_included": len(annual["sectors"]),
                "total_gva_sar_million": round(total_gva, 2),
                **{metric: round(annual[metric], 2) for metric in metrics},
                "sectors": [
                    {
                        "sector_code": sector["sector_code"],
                        "sector_name": sector["sector_name"],
                        "gva_share_s_curve": sector["gva_share_s_curve"],
                        "gva_sar_million": round(sector["gva_sar_million"], 2),
                        "share_percent": (
                            round(sector["gva_sar_million"] / total_gva * 100, 2) if total_gva else 0
                        ),
                        **{metric: round(sector[metric], 2) for metric in metrics},
                    }
                    for sector in annual["sectors"]
                ],
            }
        )

    return {
        "region": payload.region,
        "metric": "GVA",
        "unit": "SAR million",
        "aggregation": "Sum of sector outputs calculated from each sector's selected assumptions.",
        "annual_results": annual_results,
    }


@app.get("/api/v3/scenario", tags=["Version 3"])
def get_v3_scenario(
    sector_code: str = Query(...),
    gva_percentile: float = Query(..., ge=0, le=1),
    gva_emp_delta_percentile: float = Query(..., ge=0, le=1),
    emp_pop_delta_percentile: float = Query(..., ge=0, le=1),
    region: str = Query(default="Riyadh"),
):
    """Return one precomputed Version 3 scenario and all annual records."""
    connection = get_database()
    if not v3_available(connection):
        raise HTTPException(status_code=503, detail="Version 3 scenario database has not been generated.")

    scenario = connection.execute(
        """
        SELECT
            scenario_id, workbook_version, region, sector_code, sector_name,
            workbook_column, gva_percentile, gva_share_result,
            gva_emp_delta_percentile, gva_emp_delta_result,
            emp_pop_delta_percentile, emp_pop_delta_result
        FROM v3.scenario_master
        WHERE LOWER(region) = LOWER(?)
          AND sector_code = ?
          AND gva_percentile = ?
          AND gva_emp_delta_percentile = ?
          AND emp_pop_delta_percentile = ?
        """,
        [
            region,
            sector_code,
            round(gva_percentile, 2),
            round(gva_emp_delta_percentile, 2),
            round(emp_pop_delta_percentile, 2),
        ],
    ).fetchone()
    if scenario is None:
        raise HTTPException(status_code=404, detail="No precomputed Version 3 scenario matches the selection.")

    annual = connection.execute(
        """
        SELECT
            year, gva_share_s_curve, gva_sar_million, employment_saudi,
            employment_non_saudi, employment_total, population, income,
            consumption, sector_gva_sar_million, sector_employment_saudi,
            sector_employment_non_saudi, sector_employment_total,
            sector_population, sector_income, sector_consumption
        FROM v3.scenario_annual_results
        WHERE scenario_id = ?
        ORDER BY year
        """,
        [scenario[0]],
    ).fetchall()

    scenario_keys = (
        "scenario_id", "workbook_version", "region", "sector_code", "sector_name", "workbook_column",
        "gva_percentile", "gva_share_result", "gva_emp_delta_percentile", "gva_emp_delta_result",
        "emp_pop_delta_percentile", "emp_pop_delta_result",
    )
    annual_keys = (
        "year", "gva_share_s_curve", "gva_sar_million", "employment_saudi", "employment_non_saudi",
        "employment_total", "population", "income", "consumption", "sector_gva_sar_million",
        "sector_employment_saudi", "sector_employment_non_saudi", "sector_employment_total",
        "sector_population", "sector_income", "sector_consumption",
    )
    return {
        "scenario": dict(zip(scenario_keys, scenario)),
        "annual_results": [dict(zip(annual_keys, row)) for row in annual],
    }


# ============================================================
# V1/V2 ENDPOINTS (CSV, optional)
# ============================================================
@app.get("/api/filters", tags=["Filters"])
def get_filters():
    """Return available regions, years, NACE codes, sectors and indicators."""
    connection = get_legacy_database()
    regions = [row[0] for row in connection.execute(
        "SELECT DISTINCT Region FROM riyadh_gva ORDER BY Region").fetchall()]
    years = [row[0] for row in connection.execute(
        "SELECT DISTINCT Year FROM riyadh_gva ORDER BY Year").fetchall()]
    nace_records = connection.execute(
        "SELECT DISTINCT NACE_Code, Sector_Name FROM riyadh_gva ORDER BY NACE_Code").fetchall()
    sectors = [row[0] for row in connection.execute(
        "SELECT DISTINCT Sector_Name FROM riyadh_gva ORDER BY Sector_Name").fetchall()]

    return {
        "regions": regions,
        "years": years,
        "nace_codes": [{"nace_code": row[0], "sector_name": row[1]} for row in nace_records],
        "sectors": sectors,
        "indicators": [{"code": code, "name": name, "unit": unit} for code, (_, name, unit) in METRICS.items()],
    }


@app.get("/api/gva", tags=["Economic Data"])
def get_gva(
    year: int = Query(..., description="Forecast year"),
    region: str = Query(default="Riyadh", description="Selected geography"),
    nace_code: Optional[str] = Query(default=None, description="Optional NACE sector code"),
    sector: Optional[str] = Query(default=None, description="Optional aggregated sector name"),
):
    """Return GVA records for a selected region and year."""
    connection = get_legacy_database()
    query = """
        SELECT Region, Year, NACE_Code, Sector_Name, GVA_Value
        FROM riyadh_gva
        WHERE LOWER(Region) = LOWER(?) AND Year = ?
    """
    parameters = [region, year]
    if nace_code is not None:
        query += " AND LOWER(NACE_Code) = LOWER(?)"
        parameters.append(nace_code)
    if sector is not None:
        query += " AND LOWER(Sector_Name) = LOWER(?)"
        parameters.append(sector)
    query += " ORDER BY NACE_Code"

    records = connection.execute(query, parameters).fetchall()
    if not records:
        raise HTTPException(status_code=404, detail="No GVA data was found for the selected region, year and sector.")

    results = [
        {"region": row[0], "year": row[1], "nace_code": row[2], "sector_name": row[3],
         "gva_value": round(float(row[4]), 2), "unit": "SAR million"}
        for row in records
    ]
    return {
        "selection": {"region": region, "year": year, "nace_code": nace_code, "sector": sector,
                      "indicator": "Gross Value Added"},
        "record_count": len(results),
        "results": results,
    }


@app.get("/api/kpis", tags=["Economic Data"])
def get_kpis(
    year: int = Query(..., description="Forecast year"),
    nace_code: str = Query(..., description="Selected NACE sector code"),
    region: str = Query(default="Riyadh", description="Selected geography"),
    metric: str = Query(default="gva", description="Metric code"),
):
    """Return the main V1 KPI values for a selected NACE code and year."""
    connection = get_legacy_database()
    metric_column, metric_name, metric_unit = metric_definition(metric)

    selected_record = connection.execute(
        f"""
        SELECT Region, Year, NACE_Code, Sector_Name, {metric_column}
        FROM riyadh_gva
        WHERE LOWER(Region) = LOWER(?) AND Year = ? AND LOWER(NACE_Code) = LOWER(?)
        LIMIT 1
        """,
        [region, year, nace_code],
    ).fetchone()
    if selected_record is None:
        raise HTTPException(status_code=404,
                            detail="No KPI record was found for the selected region, year and NACE code.")

    base_record = connection.execute(
        f"""
        SELECT Year, {metric_column}
        FROM riyadh_gva
        WHERE LOWER(Region) = LOWER(?) AND LOWER(NACE_Code) = LOWER(?)
        ORDER BY Year
        LIMIT 1
        """,
        [region, nace_code],
    ).fetchone()
    total_record = connection.execute(
        f"SELECT SUM({metric_column}) FROM riyadh_gva WHERE LOWER(Region) = LOWER(?) AND Year = ?",
        [region, year],
    ).fetchone()

    selected_value = float(selected_record[4])
    base_year = int(base_record[0])
    base_value = float(base_record[1])
    total_value = float(total_record[0])
    growth_from_base = (selected_value - base_value) / base_value * 100 if base_value != 0 else None
    share_of_total = selected_value / total_value * 100 if total_value != 0 else None

    return {
        "region": selected_record[0],
        "year": selected_record[1],
        "nace_code": selected_record[2],
        "sector_name": selected_record[3],
        "indicator": metric_name,
        "gva_value": round(selected_value, 2),
        "metric_value": round(selected_value, 2),
        "metric": metric,
        "metric_name": metric_name,
        "unit": metric_unit,
        "base_year": base_year,
        "base_year_gva": round(base_value, 2),
        "growth_from_base_percent": round(growth_from_base, 2) if growth_from_base is not None else None,
        "share_of_total_percent": round(share_of_total, 2) if share_of_total is not None else None,
    }


@app.get("/api/analysis", tags=["Economic Data"])
def get_analysis(
    year: int = Query(...),
    nace_code: str = Query(...),
    region: str = Query(default="Riyadh"),
    metric: str = Query(default="gva"),
):
    """V2 alias for the selected metric KPI response."""
    return get_kpis(year=year, nace_code=nace_code, region=region, metric=metric)


@app.get("/api/sectors", tags=["Economic Data"])
def get_sector_distribution(
    year: int = Query(..., description="Forecast year"),
    region: str = Query(default="Riyadh", description="Selected geography"),
    metric: str = Query(default="gva", description="Metric code"),
):
    """Return aggregated sector values and percentage shares."""
    connection = get_legacy_database()
    metric_column, metric_name, metric_unit = metric_definition(metric)
    records = connection.execute(
        f"""
        WITH sector_totals AS (
            SELECT Sector_Name, SUM({metric_column}) AS Sector_GVA
            FROM riyadh_gva
            WHERE LOWER(Region) = LOWER(?) AND Year = ?
            GROUP BY Sector_Name
        ),
        economy_total AS (SELECT SUM(Sector_GVA) AS Total_GVA FROM sector_totals)
        SELECT
            sector_totals.Sector_Name,
            sector_totals.Sector_GVA,
            CASE WHEN economy_total.Total_GVA = 0 THEN 0
                 ELSE (sector_totals.Sector_GVA / economy_total.Total_GVA) * 100 END AS Sector_Share
        FROM sector_totals CROSS JOIN economy_total
        ORDER BY sector_totals.Sector_GVA DESC
        """,
        [region, year],
    ).fetchall()
    if not records:
        raise HTTPException(status_code=404,
                            detail="No sector distribution data was found for the selected region and year.")

    results = [
        {"sector_name": row[0], "gva_value": round(float(row[1]), 2),
         "share_percent": round(float(row[2]), 2), "unit": metric_unit}
        for row in records
    ]
    return {
        "region": region,
        "year": year,
        "indicator": metric_name,
        "metric": metric,
        "unit": metric_unit,
        "radar_maximum": round(max(r["gva_value"] for r in results) * 1.1, 2),
        "sectors": results,
    }


@app.get("/api/distribution", tags=["Economic Data"])
def get_distribution(
    year: int = Query(...),
    region: str = Query(default="Riyadh"),
    metric: str = Query(default="gva"),
):
    """V2 alias for metric-dependent sector distribution."""
    return get_sector_distribution(year=year, region=region, metric=metric)


@app.get("/api/trend", tags=["Economic Data"])
def get_gva_trend(
    nace_code: str = Query(..., description="Selected NACE sector code"),
    region: str = Query(default="Riyadh", description="Selected geography"),
    metric: str = Query(default="gva", description="Metric code"),
):
    """Return the full annual trend for one NACE code."""
    connection = get_legacy_database()
    metric_column, metric_name, metric_unit = metric_definition(metric)
    records = connection.execute(
        f"""
        SELECT Region, Year, NACE_Code, Sector_Name, {metric_column}
        FROM riyadh_gva
        WHERE LOWER(Region) = LOWER(?) AND LOWER(NACE_Code) = LOWER(?)
        ORDER BY Year
        """,
        [region, nace_code],
    ).fetchall()
    if not records:
        raise HTTPException(status_code=404, detail="No trend data was found for the selected region and NACE code.")

    return {
        "region": records[0][0],
        "nace_code": records[0][2],
        "sector_name": records[0][3],
        "indicator": metric_name,
        "metric": metric,
        "unit": metric_unit,
        "trend": [
            {"year": row[1], "gva_value": round(float(row[4]), 2), "metric_value": round(float(row[4]), 2)}
            for row in records
        ],
    }


# ============================================================
# GEOJSON ENDPOINT
# ============================================================
@app.get("/api/boundaries", tags=["Geography"])
def get_boundaries():
    """Return the processed Saudi administrative boundaries as GeoJSON."""
    if not BOUNDARIES_PATH.exists():
        raise HTTPException(status_code=404,
                            detail="The Saudi boundaries GeoJSON file was not found. Run process_data.py first.")
    return FileResponse(path=str(BOUNDARIES_PATH), media_type="application/geo+json",
                        filename="saudi_boundaries.geojson")


# ============================================================
# SINGLE-URL FRONTEND
# ============================================================
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ============================================================
# LOCAL DEVELOPMENT SERVER
# ============================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
