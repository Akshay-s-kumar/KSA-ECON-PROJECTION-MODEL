from pathlib import Path

import geopandas as gpd
import pandas as pd


# Resolve folders relative to the data_pipeline root.
DATA_PIPELINE_DIR = Path(__file__).resolve().parent
EXCEL_PATH = (
    DATA_PIPELINE_DIR
    / "raw_excel"
    / "55007720_M14_Project_Grant_Projection_Model.xlsx"
)
GIS_FOLDER = DATA_PIPELINE_DIR / "raw_gis"
PROCESSED_DIR = DATA_PIPELINE_DIR / "processed"


def process_excel():
    """Extract and restructure Riyadh GVA projection data."""

    print("Reading Excel projection model...")

    if not EXCEL_PATH.exists():
        raise FileNotFoundError(
            f"Excel file not found:\n{EXCEL_PATH}\n\n"
            "Place the workbook inside data_pipeline/raw_excel/."
        )

    # Read years 2020 to 2035 from the agreed worksheet
    df_gva = pd.read_excel(
        EXCEL_PATH,
        sheet_name="Riyadh_GVA_Emp_NACE Sector",
        header=4,
        nrows=16,
        engine="openpyxl",
    )

    # Clean column headings
    df_gva.columns = [
        str(column).strip()
        for column in df_gva.columns
    ]

    nace_columns = [
        "A",
        "B-E",
        "F",
        "G-I",
        "J",
        "K",
        "L",
        "M_N",
        "O-Q",
        "R-U",
    ]

    required_columns = ["Year"] + nace_columns
    missing_columns = [
        column
        for column in required_columns
        if column not in df_gva.columns
    ]

    if missing_columns:
        print("\nColumns found in the worksheet:")
        print(df_gva.columns.tolist())

        raise ValueError(
            "\nThe following required columns were not found: "
            + ", ".join(missing_columns)
        )

    # Keep only valid year records
    df_gva["Year"] = pd.to_numeric(
        df_gva["Year"],
        errors="coerce",
    )

    df_gva = df_gva.dropna(subset=["Year"]).copy()
    df_gva["Year"] = df_gva["Year"].astype(int)

    # Reshape the table from wide format to long format
    df_gva_long = pd.melt(
        df_gva,
        id_vars=["Year"],
        value_vars=nace_columns,
        var_name="NACE_Code",
        value_name="GVA_Value",
    )

    # Ensure GVA is numeric
    df_gva_long["GVA_Value"] = pd.to_numeric(
        df_gva_long["GVA_Value"],
        errors="coerce",
    )

    # Apply the agreed sector mapping
    sector_mapping = {
        "A": "Agriculture",
        "B-E": "Industry",
        "F": "Industry",
        "G-I": "Consumer services",
        "J": (
            "Transport, storage, information "
            "& communication services"
        ),
        "K": "Financial & business services",
        "L": "Financial & business services",
        "M_N": "Financial & business services",
        "O-Q": "Public services",
        "R-U": "Consumer services",
    }

    df_gva_long["Sector_Name"] = (
        df_gva_long["NACE_Code"].map(sector_mapping)
    )

    df_gva_long["Region"] = "Riyadh"

    # Arrange output columns
    df_gva_long = df_gva_long[
        [
            "Region",
            "Year",
            "NACE_Code",
            "Sector_Name",
            "GVA_Value",
        ]
    ]

    output_path = PROCESSED_DIR / "riyadh_gva_clean.csv"
    df_gva_long.to_csv(output_path, index=False)

    print(f"[SUCCESS] GVA data saved to:\n{output_path}")
    print(f"[INFO] Number of output rows: {len(df_gva_long)}")
    print(
        f"[INFO] Year range: "
        f"{df_gva_long['Year'].min()} to "
        f"{df_gva_long['Year'].max()}"
    )


def process_gis():
    """Convert the first shapefile to EPSG:4326 GeoJSON."""

    if not GIS_FOLDER.exists():
        print(
            "[WARNING] raw_gis folder does not exist. "
            "Skipping GeoJSON export."
        )
        return

    shapefiles = sorted(GIS_FOLDER.glob("*.shp"))

    if not shapefiles:
        print(
            "[WARNING] No .shp file found in raw_gis/. "
            "Skipping GeoJSON export."
        )
        return

    # Use the first administrative level so regional features such as
    # Riyadh are available to the frontend. Fall back to the first
    # shapefile when a level-one file is not present.
    level_one_shapefiles = [
        path
        for path in shapefiles
        if path.stem.lower().endswith("_1")
    ]
    shapefile_path = (
        level_one_shapefiles[0]
        if level_one_shapefiles
        else shapefiles[0]
    )
    print(f"Reading shapefile:\n{shapefile_path}")

    gdf = gpd.read_file(shapefile_path)

    if gdf.empty:
        raise ValueError(
            f"The shapefile contains no geographic features: "
            f"{shapefile_path}"
        )

    if gdf.crs is None:
        raise ValueError(
            "The shapefile has no coordinate reference system. "
            "Confirm that its matching .prj file is present."
        )

    # Convert to the web-map coordinate system
    gdf = gdf.to_crs(epsg=4326)

    # Remove empty or invalid-null geometries
    gdf = gdf[
        gdf.geometry.notna()
        & ~gdf.geometry.is_empty
    ].copy()

    output_path = PROCESSED_DIR / "saudi_boundaries.geojson"
    gdf.to_file(
        output_path,
        driver="GeoJSON",
        encoding="utf-8",
    )

    print(f"[SUCCESS] GIS boundaries saved to:\n{output_path}")
    print(f"[INFO] Number of geographic features: {len(gdf)}")


def process_data():
    """Run the complete V1 processing pipeline."""

    print("=" * 60)
    print("Starting Riyadh Economic Digital Twin data processing")
    print("=" * 60)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    process_excel()
    process_gis()

    print("=" * 60)
    print("[SUCCESS] Data and GIS processing completed")
    print("=" * 60)


if __name__ == "__main__":
    process_data()