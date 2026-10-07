"""Version 4 workbook layout. Every cell address used by the extraction lives here."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
WORKBOOK_PATH = PROJECT_DIR / "raw_excel" / "55007720_M14_Project_Grant_Projection_Model.xlsx"
REFERENCE_DB_PATH = PROJECT_DIR / "processed" / "v4_reference.duckdb"

# sector_code -> (GVA share sheet, GVA-employment delta sheet)
# Align these codes with the sector codes used in v3_model.py.
SECTORS = {
    "A":   ("NUTS3-A",   "GVA_EMP_DELTA-3-A"),
    "B-E": ("NUTS3-B-E", "GVA_EMP_DELTA-3-B-E"),
    "F":   ("NUTS3-F",   "GVA_EMP_DELTA-3-F"),
    "G-I": ("NUTS3-G-I", "GVA_EMP_DELTA-3-G-I"),
    "J":   ("NUTS3-J",   "GVA_EMP_DELTA-3-J"),
    "K":   ("NUTS3-K",   "GVA_EMP_DELTA-3-K"),
    "L":   ("NUTS3-L",   "GVA_EMP_DELTA-3-L"),
    "M-N": ("NUTS3-M_N", "GVA_EMP_DELTA-3-M-N"),
    "O-Q": ("NUTS3-O-Q", "GVA_EMP_DELTA-3-O-Q"),
    "R-U": ("NUTS3-R-U", "GVA_EMP_DELTA-3-R-U"),
}

# ---- NUTS3-* sheets (GVA share) -------------------------------------------
GVA = {
    "first_row": 2, "last_row": 1351,
    "id_col": "A",
    "share_first_col": "D", "share_last_col": "AG",     # 1995..2024, header in row 1
    "nuts3_pop_col": "BF", "nuts2_pop_col": "BG",
    "band_cells": {"nuts3_min": "BH1", "nuts3_max": "BH2", "nuts2_min": "BH3", "nuts2_max": "BH4"},
    # Excel checkpoints: BF = percentile, BH = MAX(BI:CL) of the filtered PERCENTILEs
    "check_first_row": 1375, "check_last_row": 1394,
    "check_percentile_col": "BF", "check_value_col": "BH",
}

# ---- GVA_EMP_DELTA-3-* sheets ---------------------------------------------
GVA_EMP = {
    "first_row": 3, "last_row": 1352,
    "id_col": "A",
    "value_col": "AF",                                  # 2024 delta
    "nuts3_pop_col": "AM", "nuts2_pop_col": "AN",
    "band_cells": {"nuts3_min": "AM1", "nuts3_max": "AM2", "nuts2_min": "AN1", "nuts2_max": "AN2"},
}

# ---- Emp-Pop-Delta-3 (single sheet) ----------------------------------------
EMP_POP = {
    "sheet": "Emp-Pop-Delta-3",
    "first_row": 2, "last_row": 1351,
    "id_col": "A",
    "value_col": "AF",                                  # 2024 delta
    "nuts3_pop_col": "AJ", "nuts2_pop_col": "AK",
    # Labels in AG1/AH1 read "NUTS 2"/"NUTS 3", but the formulas apply AG to AJ (NUTS3) and AH to AK (NUTS2).
    "band_cells": {"nuts3_min": "AG2", "nuts3_max": "AG3", "nuts2_min": "AH2", "nuts2_max": "AH3"},
    # Excel checkpoints: AP = percentile, AR = PERCENTILE(FILTER(AF, both bands), AP)
    "check_first_row": 1, "check_last_row": 60,
    "check_percentile_col": "AP", "check_value_col": "AR",
}

# Percentile ladders offered by the sliders (same as Version 3)
GVA_PERCENTILES = [round(p / 100, 2) for p in range(5, 101, 5)]        # 5%..100%  (20)
DELTA_PERCENTILES = [round(p / 100, 2) for p in range(0, 101, 5)]      # 0%..100%  (21)
