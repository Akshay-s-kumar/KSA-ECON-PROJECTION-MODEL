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

NACE_CODES = [
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

SECTOR_NAMES = {
    "A": "Agriculture",
    "B-E": "Industry",
    "F": "Construction",
    "G-I": "Wholesale, retail, transport, accommodation and food",
    "J": "Information and communication",
    "K": "Financial and insurance activities",
    "L": "Real estate activities",
    "M_N": "Professional, scientific, technical, administration and support",
    "O-Q": "Public administration, defence, education, health and social work",
    "R-U": "Other services",
}


def process_excel():
    """Extract the hybrid GVA and employment projection tables."""

    print("Reading Excel projection model...")

    if not EXCEL_PATH.exists():
        raise FileNotFoundError(
            f"Excel file not found:\n{EXCEL_PATH}\n\n"
            "Place the workbook inside data_pipeline/raw_excel/."
        )

    worksheet = pd.read_excel(
        EXCEL_PATH,
        sheet_name="GVA-EmploymentShares&Projec_HYB",
        header=None,
        engine="openpyxl",
    )

    def extract_table(
        start_row: int,
        end_row: int,
        value_start_column: int,
        value_end_column: int,
        value_name: str,
    ) -> pd.DataFrame:
        rows = worksheet.iloc[start_row:end_row + 1].copy()
        years = pd.to_numeric(rows.iloc[:, 1], errors="coerce")
        values = rows.iloc[:, value_start_column:value_end_column + 1].apply(
            pd.to_numeric,
            errors="coerce",
        )
        values.columns = NACE_CODES
        values.insert(0, "Year", years)
        values = values.dropna(subset=["Year"]).copy()
        values["Year"] = values["Year"].astype(int)
        return values.melt(
            id_vars=["Year"],
            var_name="NACE_Code",
            value_name=value_name,
        )

    gva_shares = extract_table(19, 66, 2, 11, "GVA_Share")
    gva_values = extract_table(19, 66, 12, 21, "GVA_Value")
    employment_values = extract_table(75, 122, 2, 11, "Employment_Value")

    data = gva_values.merge(
        gva_shares,
        on=["Year", "NACE_Code"],
        validate="one_to_one",
    ).merge(
        employment_values,
        on=["Year", "NACE_Code"],
        validate="one_to_one",
    )

    data["Employment_Share"] = (
        data["Employment_Value"]
        / data.groupby("Year")["Employment_Value"].transform("sum")
    )
    data["Productivity"] = (
        data["GVA_Value"] * 1000 / data["Employment_Value"]
    )
    data["Region"] = "Riyadh"
    data["Sector_Name"] = data["NACE_Code"].map(SECTOR_NAMES)

    data = data[
        [
            "Region",
            "Year",
            "NACE_Code",
            "Sector_Name",
            "GVA_Value",
            "Employment_Value",
            "Productivity",
            "GVA_Share",
            "Employment_Share",
        ]
    ].sort_values(["Year", "NACE_Code"])

    if data[["GVA_Value", "Employment_Value"]].isna().any().any():
        raise ValueError(
            "The hybrid worksheet contains missing GVA or employment values."
        )

    output_path = PROCESSED_DIR / "riyadh_economic_projection.csv"
    data.to_csv(output_path, index=False)

    # Keep the V1 output available for existing API consumers.
    legacy_path = PROCESSED_DIR / "riyadh_gva_clean.csv"
    data[
        ["Region", "Year", "NACE_Code", "Sector_Name", "GVA_Value"]
    ].to_csv(legacy_path, index=False)

    print(f"[SUCCESS] Economic projection data saved to:\n{output_path}")
    print(f"[INFO] Number of output rows: {len(data)}")
    print(
        f"[INFO] Year range: "
        f"{data['Year'].min()} to "
        f"{data['Year'].max()}"
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
    """Run the complete Version 2 processing pipeline."""

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