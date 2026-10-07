# KSA Economic Projection Model: Version 4

**Branch:** `version-4` (created from `version-3`)
**Repository:** `Akshay-s-kumar/KSA-ECON-PROJECTION-MODEL`
**Scope:** Riyadh only (same as Version 3)
**Source workbook:** `55007720_M14_Project_Grant_Projection_Model.xlsx`

---

## 1. Goal

Version 4 = **Version 3 + four population inputs**.

Everything from Version 3 stays the same: the sliders, results panel, map, S-curves and All-sectors view. Version 4 adds one new control panel where the user sets the **comparator region population band**:

| Input | Meaning |
|---|---|
| NUTS3 population min | Lower limit for NUTS3 comparator regions |
| NUTS3 population max | Upper limit for NUTS3 comparator regions |
| NUTS2 population min | Lower limit for the parent NUTS2 region |
| NUTS2 population max | Upper limit for the parent NUTS2 region |

These four figures decide **which comparator regions** the percentile benchmarks are built from. For example, the "50th percentile" means "the median of comparable regions in this population band".

---

## 2. Why Version 4 is needed

In Version 3:

- Python opened the workbook with `data_only=True` and read Excel's **last saved results** (old block at rows 1353–1372).
- The app never read BH1:BH4 and applied **no population filter of its own**.
- The percentiles depended on whichever band was set when the workbook was last saved, and the app had no record of it.
- `scenario_master` and the scenario ID did not store the band, so results could not be traced back to a comparator set.

Version 4 makes the band **visible, editable and recorded**.

---

## 3. What changes and what stays

| Area | Version 3 | Version 4 |
|---|---|---|
| Percentile sliders (GVA, GVA-Employment, Employment-Population) | Yes | Same |
| Population band | Fixed, from the saved workbook | **4 inputs in the app** |
| Lookup tables (percentile → value) | Read from Excel's saved results | **Calculated in Python** from raw region rows |
| Projection chain (S-curve → GVA → jobs → income) | Python | Same code |
| Results panel, map, S-curves | As built | Same, plus a **"N regions pass"** count |
| Database | `v3_scenarios.duckdb`: 4.2M precomputed result rows | `v4_reference.duckdb`: raw inputs only, a few MB |
| Calculation | Done in advance | Done in memory on each request |

---

## 4. Workbook logic being reproduced

### 4.1 GVA share sheets (`NUTS3-*`, e.g. `NUTS3-G-I`)

| Item | Cells |
|---|---|
| Region rows | 2 to 1351 |
| Shares (GVA of NUTS3 ÷ GVA of NUTS0), 1995–2024 | Columns **D:AG** |
| NUTS3 population | Column **BF** |
| NUTS2 population | Column **BG** |
| Band limits | **BH1** NUTS3 min, **BH2** NUTS3 max, **BH3** NUTS2 min, **BH4** NUTS2 max |
| Percentile per year | **BI1375:CL1394** |
| Final value (max across years) | **BH1375:BH1394** |
| Percentile level | Column **BF** (rows 1375 onwards) |

Example workbook formula:

```excel
=PERCENTILE(
   FILTER(D$2:D$1351,
     ($BF$2:$BF$1351>=$BH$1)*($BF$2:$BF$1351<=$BH$2)*
     ($BG$2:$BG$1351>=$BH$3)*($BG$2:$BG$1351<=$BH$4)),
   $BF1375)
```

Steps:

1. Filter the regions by the band.
2. Calculate the percentile for each year (BI:CL).
3. Take the **maximum** across years (BH1375:BH1394).

### 4.2 GVA-Employment sheets (`GVA_EMP_DELTA-3-*`)

| Item | Cells |
|---|---|
| NUTS3 band | **AM1:AM2** |
| NUTS2 band | **AN1:AN2** |
| Values filtered | Column **AF** |
| Special rule | **−1 is replaced with −0.99** (kept from the workbook) |

### 4.3 Employment-Population sheet (`Emp-Pop-Delta-3`, single sheet)

| Item | Cells |
|---|---|
| NUTS3 band | **AG2:AG3** |
| NUTS2 band | **AH2:AH3** |
| Values filtered | Column **AF** (2024) |
| Population columns used | **AJ / AK** |
| Workbook result | Column **AR** |

### 4.4 Filtering rules

- A region must pass **both** the NUTS3 and the NUTS2 limits.
- Limits are **inclusive** (≥ and ≤), the same as the FILTER formula.
- **Blanks are left out.** They are never treated as 0.
- **Zeros that Excel stores are kept**, so the results match the workbook.

---

## 5. Findings from the workbook check

1. **Only one comparator region passes the current GVA band.**
   With NUTS3 200,000–300,000 and NUTS2 7.5M–12.5M, only **ITC49** passes out of 1,350 regions. So every percentile gives the same value. For example, G-I is 1.828% from 5% to 100%. The GVA slider will have no effect until the band is widened.
2. **Each lookup uses a different band in the workbook.**
   GVA and GVA-Employment use NUTS3 200k–300k. Employment-Population uses NUTS3 **400k–700k**.
3. **Emp-Pop labels may be swapped.**
   AG1 says "NUTS 2" and AH1 says "NUTS 3", but the formulas apply AG to the NUTS3 population and AH to the NUTS2 population.
4. **Missing data shows as 0, not blank.**
   The share formulas use `IFERROR(…,0)`.
5. **The old V3 block (rows 1353–1372)** used the NUTS3 filter only, with `>` and `<`, and its 100% value was 1.0 (a 100% share).

> These points need confirming with the model owner before results are presented.

---

## 6. Agreed decisions

| Decision | Choice |
|---|---|
| Calculation approach | **Option 3:** V3 logic calculated on request from the raw comparator rows |
| Bands | **One shared band by default**, with an "Advanced" toggle for separate bands |
| Zeros and 1.0 shares | **Match Excel first** so the test passes. Cleaning can be added later as an option |
| Database | **DuckDB** (kept), used for reference inputs instead of precomputed results |

### Options considered

| Option | How it works | Outcome |
|---|---|---|
| 1. Rebuild per band | Regenerate the V3 database for each band | Rejected: minutes per change |
| 2. Preset bands | Precompute 5–10 named bands | Rejected: no custom numbers, 4.2M rows per band |
| **3. Calculate on request** | Filter, build lookups and project in memory | **Chosen:** fast, any numbers, small database |

---

## 7. Architecture

```text
Excel workbook
     │  (read once)
     ▼
extract_v4.py ──► v4_reference.duckdb
                     │  (loaded once at API start, into numpy arrays)
                     ▼
              FastAPI backend (main.py)
                     │  filter by band → build lookup tables → V3 projection chain
                     ▼
              static_frontend (HTML / CSS / JS / MapLibre GL JS)
```

### 7.1 Database: `v4_reference.duckdb`

| Table content | Purpose |
|---|---|
| Comparator regions | For the 10 sectors: region ID, NUTS3 population, NUTS2 population, 30 years of shares (D:AG), GVA-Employment and Employment-Population values |
| Riyadh base data | Base GVA, productivity, population ratio, Saudi / non-Saudi shares, income and MPC (the same inputs V3 reads) |
| Default bands | The workbook's current figures, used to pre-fill the four boxes |
| Excel check values | Saved BH1375:BH1394 and similar results, used **only** by the test |
| Timestamp | When the extraction ran |

### 7.2 Performance

| Change | What runs | Approx. time |
|---|---|---|
| One of the 4 population figures | Filter → rebuild lookups → projection | ~30–60 ms calculation |
| A percentile slider | Lookup and projection only (filtered regions cached) | A few ms |
| Year slider | Nothing on the server | Instant |
| A setting used before | Served from cache | Instant |

The benchmark (~30 ms) was run on synthetic data at the workbook's size (10 sectors × 1,350 regions × 30 years). The first uncached version took 0.85 s and was vectorised. Times on your laptop may differ slightly.

---

## 8. Files

| File | Role | Status |
|---|---|---|
| `v4_config.py` | All sheet names and cell addresses in one place | Built |
| `extract_v4.py` | Reads the workbook once and writes `v4_reference.duckdb` | Built |
| `v4_lookups.py` | Filters regions by the 4 figures and builds the lookup tables, with caching | Built |
| `test_v4_lookups.py` | Checks the formula rules, then checks results against Excel | Built: synthetic tests pass, **Excel-match test still to run locally** |
| `static_frontend/app.js` | V4 UI logic | Built |
| `static_frontend/index.html` | Add the Comparator regions panel | Snippet provided |
| `static_frontend/style.css` | Band panel styles | Snippet provided |
| `backend_api/main.py` | API endpoints for on-request calculation | **To do** |

### Suggested folder layout

```text
riyadh_digital_twin/
├── data_pipeline/
│   ├── raw_excel/          # workbook (not committed)
│   ├── processed/          # v4_reference.duckdb (not committed)
│   ├── v4_config.py
│   └── extract_v4.py
├── backend_api/
│   ├── main.py
│   ├── v4_lookups.py
│   └── test_v4_lookups.py
├── static_frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── .gitignore
└── README_V4.md
```

---

## 9. User interface

The V3 layout is kept, including the Montserrat font, the dark navy glassmorphism style, the KSA map that zooms to Riyadh, the right results panel and the fixed title **"KSA Economic Projection Model"**.

### 9.1 New left-panel section (top)

```text
Comparator regions                    Reset to workbook
                       Min          Max
NUTS3 population   [ 200000 ]   [ 300000 ]
NUTS2 population   [ 7500000 ]  [ 12500000 ]
⚠ GVA 1 · GVA–Emp 1 · Emp–Pop 0 regions pass. Widen the range.
──────────────────────────────────────────
Region / Sector dropdowns
GVA percentile             ──●──  50%
GVA–employment delta       ──●──  50%
Employment–population      ──●──  50%
```

### 9.2 Behaviour

| Action | Result |
|---|---|
| Page opens | Boxes fill with the workbook figures and results load straight away |
| Change a figure, then press Enter or click out | Results and region count update automatically |
| Move a slider | Results update 250 ms after you stop (same as V3) |
| Click **Reset to workbook** | Workbook figures return and results update |
| Min higher than max, or an empty box | Box turns red and previous results stay |
| No regions pass | Red message: widen the range. Previous results stay |
| Fewer than 10 regions pass | Amber warning: percentiles will barely change |
| Right panel | Scenario details show the **comparator band** used |
| Run button | Renamed **Refresh Projection**. Optional, because everything updates on its own |

Population boxes update on **Enter or blur**, not on every keystroke, so a half-typed number such as "2" doesn't briefly filter out almost every region.

---

## 10. How to run (local)

```bash
# 1. Switch to the branch
git checkout version-4

# 2. Build the reference database (close the workbook in Excel first)
python data_pipeline/extract_v4.py

# 3. Run the tests, including the Excel-match test
python -m pytest backend_api/test_v4_lookups.py -v

# 4. Start the API
cd backend_api
uvicorn main:app --reload
```

Re-run step 2 only when values **in the workbook** change. It takes seconds.

---

## 11. Testing plan

| Test | Checks | Status |
|---|---|---|
| Filter rules | Inclusive limits, both NUTS3 and NUTS2 must pass | Pass (synthetic) |
| Blank handling | Blanks excluded, stored zeros kept | Pass (synthetic) |
| GVA-Employment rule | −1 becomes −0.99 | Pass (synthetic) |
| Band errors | No region passes, or min higher than max, gives a clear message | Pass (synthetic) |
| **Excel match** | Python lookups equal the saved BH1375:BH1394 and equivalent values | **Run on your machine** |
| Projection chain | V4 at workbook defaults gives the same results as V3 | To do |

---

## 12. Git workflow

```bash
# Save work
git add -A
git commit -m "Describe what you changed"
git push
```

`.gitignore` must include:

```gitignore
*.duckdb
*.duckdb.wal
*.db
~$*
.venv/
__pycache__/
```

- Close the Excel workbook before `git add -A` so the `~$` lock file isn't picked up.
- Large DuckDB files are never committed. They are rebuilt from the scripts. (GitHub blocks files over 100 MB; the V3 database was 715 MB.)
- `main` stays unchanged. `version-3` is kept as it is.

---

## 13. Next steps

1. Run `extract_v4.py` and the **Excel-match test** against the real workbook.
2. Update `backend_api/main.py` with the on-request calculation endpoint.
3. Add the Comparator regions panel to `index.html` and the styles to `style.css`.
4. Confirm the open workbook points in Section 5 (single passing region, Emp-Pop labels, differing bands).
5. Add the **Advanced** toggle for separate bands per lookup.
6. Later, as an option: cleaning of zeros and 1.0 shares.
