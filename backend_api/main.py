from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from typing import Optional

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


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

METRICS = {
    "gva": ("GVA_Value", "Gross Value Added", "SAR million"),
    "employment": ("Employment_Value", "Employment", "thousand jobs"),
    "productivity": ("Productivity", "Productivity", "SAR thousand per job"),
    "gva_share": ("GVA_Share", "GVA Share", "percent"),
    "employment_share": ("Employment_Share", "Employment Share", "percent"),
}


# ============================================================
# DUCKDB CONNECTION
# ============================================================

database_connection: Optional[duckdb.DuckDBPyConnection] = None


def get_database() -> duckdb.DuckDBPyConnection:
    """
    Return the active DuckDB connection.
    """

    if database_connection is None:
        raise HTTPException(
            status_code=503,
            detail="The analytical database has not been initialized.",
        )

    return database_connection


def initialize_database() -> duckdb.DuckDBPyConnection:
    """
    Create an in-memory DuckDB database and register the processed
    Riyadh GVA CSV as a queryable view.
    """

    if not ANALYTICS_CSV_PATH.exists():
        raise FileNotFoundError(
            f"Processed economic projection file not found: {ANALYTICS_CSV_PATH}"
        )

    connection = duckdb.connect(database=":memory:")

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
        FROM read_csv_auto(
            '{csv_path_sql}',
            header = true
        )
        WHERE
            Year IS NOT NULL
            AND NACE_Code IS NOT NULL
            AND Sector_Name IS NOT NULL
            AND GVA_Value IS NOT NULL
            AND Employment_Value IS NOT NULL
        """
    )

    row_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM riyadh_gva
        """
    ).fetchone()[0]

    if row_count == 0:
        connection.close()

        raise ValueError(
            "The processed GVA CSV contains no valid records."
        )

    return connection


# ============================================================
# APPLICATION LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize DuckDB when the API starts and close it when the
    application stops.
    """

    global database_connection

    print("=" * 60)
    print("Starting Riyadh Economic Digital Twin API")
    print("=" * 60)

    database_connection = initialize_database()

    row_count = database_connection.execute(
        "SELECT COUNT(*) FROM riyadh_gva"
    ).fetchone()[0]

    year_range = database_connection.execute(
        """
        SELECT
            MIN(Year) AS minimum_year,
            MAX(Year) AS maximum_year
        FROM riyadh_gva
        """
    ).fetchone()

    print(f"[SUCCESS] DuckDB initialized with {row_count} records")
    print(
        f"[INFO] Available years: "
        f"{year_range[0]} to {year_range[1]}"
    )

    if BOUNDARIES_PATH.exists():
        print(f"[SUCCESS] Boundary file found: {BOUNDARIES_PATH}")
    else:
        print(
            f"[WARNING] Boundary file not found: "
            f"{BOUNDARIES_PATH}"
        )

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
        "Local API for querying Riyadh GVA projections and "
        "serving Saudi Arabia administrative boundaries."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

request_lock = asyncio.Lock()


def metric_definition(metric: str) -> tuple[str, str, str]:
    try:
        return METRICS[metric]
    except KeyError as error:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported metric '{metric}'. "
                f"Choose one of: {', '.join(METRICS)}."
            ),
        ) from error


@app.middleware("http")
async def serialize_database_requests(request, call_next):
    """
    Serialize local API requests because the in-memory DuckDB
    connection is shared by all endpoint handlers.
    """

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
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================
# ROOT AND HEALTH ENDPOINTS
# ============================================================

@app.get("/", tags=["System"])
def root():
    """
    Serve the single-page frontend.
    """

    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health", tags=["System"])
def health_check():
    """
    Confirm that the API, DuckDB view, CSV and GeoJSON are
    available.
    """

    connection = get_database()

    record_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM riyadh_gva
        """
    ).fetchone()[0]

    return {
        "status": "healthy",
        "database": "connected",
        "gva_csv_available": GVA_CSV_PATH.exists(),
        "analytics_csv_available": ANALYTICS_CSV_PATH.exists(),
        "boundaries_available": BOUNDARIES_PATH.exists(),
        "gva_record_count": record_count,
    }


# ============================================================
# FILTER ENDPOINT
# ============================================================

@app.get("/api/filters", tags=["Filters"])
def get_filters():
    """
    Return available regions, years, NACE codes, sectors and
    indicators for the frontend controls.
    """

    connection = get_database()

    regions = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT Region
            FROM riyadh_gva
            ORDER BY Region
            """
        ).fetchall()
    ]

    years = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT Year
            FROM riyadh_gva
            ORDER BY Year
            """
        ).fetchall()
    ]

    nace_records = connection.execute(
        """
        SELECT DISTINCT
            NACE_Code,
            Sector_Name
        FROM riyadh_gva
        ORDER BY NACE_Code
        """
    ).fetchall()

    nace_codes = [
        {
            "nace_code": row[0],
            "sector_name": row[1],
        }
        for row in nace_records
    ]

    sectors = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT Sector_Name
            FROM riyadh_gva
            ORDER BY Sector_Name
            """
        ).fetchall()
    ]

    return {
        "regions": regions,
        "years": years,
        "nace_codes": nace_codes,
        "sectors": sectors,
        "indicators": [
            {"code": code, "name": name, "unit": unit}
            for code, (_, name, unit) in METRICS.items()
        ],
    }


# ============================================================
# GVA ENDPOINT
# ============================================================

@app.get("/api/gva", tags=["Economic Data"])
def get_gva(
    year: int = Query(
        ...,
        description="Forecast year",
    ),
    region: str = Query(
        default="Riyadh",
        description="Selected geography",
    ),
    nace_code: Optional[str] = Query(
        default=None,
        description="Optional NACE sector code",
    ),
    sector: Optional[str] = Query(
        default=None,
        description="Optional aggregated sector name",
    ),
):
    """
    Return GVA records for a selected region and year.

    Either NACE code or aggregated sector name may be supplied.
    If neither is supplied, all available records for the year
    are returned.
    """

    connection = get_database()

    query = """
        SELECT
            Region,
            Year,
            NACE_Code,
            Sector_Name,
            GVA_Value
        FROM riyadh_gva
        WHERE
            LOWER(Region) = LOWER(?)
            AND Year = ?
    """

    parameters = [region, year]

    if nace_code is not None:
        query += """
            AND LOWER(NACE_Code) = LOWER(?)
        """
        parameters.append(nace_code)

    if sector is not None:
        query += """
            AND LOWER(Sector_Name) = LOWER(?)
        """
        parameters.append(sector)

    query += """
        ORDER BY NACE_Code
    """

    records = connection.execute(
        query,
        parameters,
    ).fetchall()

    if not records:
        raise HTTPException(
            status_code=404,
            detail=(
                "No GVA data was found for the selected region, "
                "year and sector."
            ),
        )

    results = [
        {
            "region": row[0],
            "year": row[1],
            "nace_code": row[2],
            "sector_name": row[3],
            "gva_value": round(float(row[4]), 2),
            "unit": "SAR million",
        }
        for row in records
    ]

    return {
        "selection": {
            "region": region,
            "year": year,
            "nace_code": nace_code,
            "sector": sector,
            "indicator": "Gross Value Added",
        },
        "record_count": len(results),
        "results": results,
    }


# ============================================================
# KPI ENDPOINT
# ============================================================

@app.get("/api/kpis", tags=["Economic Data"])
def get_kpis(
    year: int = Query(
        ...,
        description="Forecast year",
    ),
    nace_code: str = Query(
        ...,
        description="Selected NACE sector code",
    ),
    region: str = Query(
        default="Riyadh",
        description="Selected geography",
    ),
    metric: str = Query(default="gva", description="Metric code"),
):
    """
    Return the main V1 KPI values for a selected NACE code and
    year.

    Growth is calculated against the earliest available year in
    the processed dataset.
    """

    connection = get_database()
    metric_column, metric_name, metric_unit = metric_definition(metric)

    selected_record = connection.execute(
        f"""
        SELECT
            Region,
            Year,
            NACE_Code,
            Sector_Name,
            {metric_column}
        FROM riyadh_gva
        WHERE
            LOWER(Region) = LOWER(?)
            AND Year = ?
            AND LOWER(NACE_Code) = LOWER(?)
        LIMIT 1
        """,
        [region, year, nace_code],
    ).fetchone()

    if selected_record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No KPI record was found for the selected "
                "region, year and NACE code."
            ),
        )

    base_record = connection.execute(
        f"""
        SELECT
            Year,
            {metric_column}
        FROM riyadh_gva
        WHERE
            LOWER(Region) = LOWER(?)
            AND LOWER(NACE_Code) = LOWER(?)
        ORDER BY Year
        LIMIT 1
        """,
        [region, nace_code],
    ).fetchone()

    total_gva_record = connection.execute(
        f"""
        SELECT
            SUM({metric_column})
        FROM riyadh_gva
        WHERE
            LOWER(Region) = LOWER(?)
            AND Year = ?
        """,
        [region, year],
    ).fetchone()

    selected_gva = float(selected_record[4])
    base_year = int(base_record[0])
    base_gva = float(base_record[1])
    total_gva = float(total_gva_record[0])

    if base_gva != 0:
        growth_from_base = (
            (selected_gva - base_gva) / base_gva
        ) * 100
    else:
        growth_from_base = None

    if total_gva != 0:
        share_of_total = (
            selected_gva / total_gva
        ) * 100
    else:
        share_of_total = None

    return {
        "region": selected_record[0],
        "year": selected_record[1],
        "nace_code": selected_record[2],
        "sector_name": selected_record[3],
        "indicator": metric_name,
        "gva_value": round(selected_gva, 2),
        "metric_value": round(selected_gva, 2),
        "metric": metric,
        "metric_name": metric_name,
        "unit": metric_unit,
        "base_year": base_year,
        "base_year_gva": round(base_gva, 2),
        "growth_from_base_percent": (
            round(growth_from_base, 2)
            if growth_from_base is not None
            else None
        ),
        "share_of_total_percent": (
            round(share_of_total, 2)
            if share_of_total is not None
            else None
        ),
    }


@app.get("/api/analysis", tags=["Economic Data"])
def get_analysis(
    year: int = Query(...),
    nace_code: str = Query(...),
    region: str = Query(default="Riyadh"),
    metric: str = Query(default="gva"),
):
    """V2 alias for the selected metric KPI response."""

    return get_kpis(
        year=year,
        nace_code=nace_code,
        region=region,
        metric=metric,
    )


# ============================================================
# SECTOR DISTRIBUTION ENDPOINT
# ============================================================

@app.get("/api/sectors", tags=["Economic Data"])
def get_sector_distribution(
    year: int = Query(
        ...,
        description="Forecast year",
    ),
    region: str = Query(
        default="Riyadh",
        description="Selected geography",
    ),
    metric: str = Query(default="gva", description="Metric code"),
):
    """
    Return aggregated sector GVA values and percentage shares.

    This endpoint provides the data required for the ECharts
    radar or spider chart.
    """

    connection = get_database()
    metric_column, metric_name, metric_unit = metric_definition(metric)

    records = connection.execute(
        f"""
        WITH sector_totals AS (
            SELECT
                Sector_Name,
                SUM({metric_column}) AS Sector_GVA
            FROM riyadh_gva
            WHERE
                LOWER(Region) = LOWER(?)
                AND Year = ?
            GROUP BY Sector_Name
        ),
        economy_total AS (
            SELECT
                SUM(Sector_GVA) AS Total_GVA
            FROM sector_totals
        )
        SELECT
            sector_totals.Sector_Name,
            sector_totals.Sector_GVA,
            CASE
                WHEN economy_total.Total_GVA = 0 THEN 0
                ELSE (
                    sector_totals.Sector_GVA
                    / economy_total.Total_GVA
                ) * 100
            END AS Sector_Share
        FROM sector_totals
        CROSS JOIN economy_total
        ORDER BY sector_totals.Sector_GVA DESC
        """,
        [region, year],
    ).fetchall()

    if not records:
        raise HTTPException(
            status_code=404,
            detail=(
                "No sector distribution data was found for "
                "the selected region and year."
            ),
        )

    results = [
        {
            "sector_name": row[0],
            "gva_value": round(float(row[1]), 2),
            "share_percent": round(float(row[2]), 2),
            "unit": metric_unit,
        }
        for row in records
    ]

    maximum_value = max(
        record["gva_value"]
        for record in results
    )

    return {
        "region": region,
        "year": year,
        "indicator": metric_name,
        "metric": metric,
        "unit": metric_unit,
        "radar_maximum": round(maximum_value * 1.1, 2),
        "sectors": results,
    }


@app.get("/api/distribution", tags=["Economic Data"])
def get_distribution(
    year: int = Query(...),
    region: str = Query(default="Riyadh"),
    metric: str = Query(default="gva"),
):
    """V2 alias for metric-dependent sector distribution."""

    return get_sector_distribution(
        year=year,
        region=region,
        metric=metric,
    )


# ============================================================
# YEAR TREND ENDPOINT
# ============================================================

@app.get("/api/trend", tags=["Economic Data"])
def get_gva_trend(
    nace_code: str = Query(
        ...,
        description="Selected NACE sector code",
    ),
    region: str = Query(
        default="Riyadh",
        description="Selected geography",
    ),
    metric: str = Query(default="gva", description="Metric code"),
):
    """
    Return the full annual GVA trend for one NACE code.
    """

    connection = get_database()
    metric_column, metric_name, metric_unit = metric_definition(metric)

    records = connection.execute(
        f"""
        SELECT
            Region,
            Year,
            NACE_Code,
            Sector_Name,
            {metric_column}
        FROM riyadh_gva
        WHERE
            LOWER(Region) = LOWER(?)
            AND LOWER(NACE_Code) = LOWER(?)
        ORDER BY Year
        """,
        [region, nace_code],
    ).fetchall()

    if not records:
        raise HTTPException(
            status_code=404,
            detail=(
                "No trend data was found for the selected "
                "region and NACE code."
            ),
        )

    return {
        "region": records[0][0],
        "nace_code": records[0][2],
        "sector_name": records[0][3],
        "indicator": metric_name,
        "metric": metric,
        "unit": metric_unit,
        "trend": [
            {
                "year": row[1],
                "gva_value": round(float(row[4]), 2),
                "metric_value": round(float(row[4]), 2),
            }
            for row in records
        ],
    }


# ============================================================
# GEOJSON ENDPOINT
# ============================================================

@app.get("/api/boundaries", tags=["Geography"])
def get_boundaries():
    """
    Return the processed Saudi administrative boundaries as a
    GeoJSON file.
    """

    if not BOUNDARIES_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "The Saudi boundaries GeoJSON file was not "
                "found. Run process_data.py first."
            ),
        )

    return FileResponse(
        path=str(BOUNDARIES_PATH),
        media_type="application/geo+json",
        filename="saudi_boundaries.geojson",
    )


# ============================================================
# SINGLE-URL FRONTEND
# ============================================================

app.mount(
    "/",
    StaticFiles(directory=str(FRONTEND_DIR), html=True),
    name="frontend",
)


# ============================================================
# LOCAL DEVELOPMENT SERVER
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )