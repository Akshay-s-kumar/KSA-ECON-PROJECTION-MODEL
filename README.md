# KSA Economic Projection Model — Version 3

## Abstract

Version 3 is an Excel-free, workbook-validated economic projection application for analysing sector-level economic outcomes in the Riyadh region. The system converts the calculation logic and reference assumptions contained in the authoritative Version 3 workbook into a Python calculation engine, precomputes the complete scenario space in DuckDB, exposes the results through a FastAPI service, and presents the outputs through an interactive map-based web interface.

The objective was not to replace the workbook as the reference model. The objective was to make its validated logic operational, repeatable, queryable, and easier to use. Excel remains the reference source for assumptions and validation, while Python performs the production calculations and the web application presents the results.

The completed scenario database contains:

- 88,200 scenario combinations
- 10 economic sectors
- 4,233,600 annual result rows
- 48 projection years per scenario
- Projection years from 2023 through 2070

The user interface allows a user to select a sector, choose percentile assumptions, run a projection, inspect sector-specific results, view the GVA S-curve, and see the selected Riyadh region highlighted on a satellite map.

---

## 1. Background and Motivation

The original Version 3 model was maintained in an Excel workbook containing:

- Sector-specific GVA share lookup tables
- GVA-to-employment assumptions
- Employment-to-population assumptions
- Riyadh economic base data
- Productivity assumptions
- Saudi and non-Saudi workforce shares
- Income assumptions
- Marginal propensity to consume assumptions
- A projected GVA share S-curve

The workbook was useful as a reference model, but it was not ideal as the long-term application platform because:

1. Scenario combinations were difficult to query consistently.
2. Repeated manual changes could introduce spreadsheet errors.
3. Results were not naturally exposed through an API.
4. A map-based interactive user experience was difficult to maintain inside Excel.
5. It was difficult to generate and validate every sector and percentile combination systematically.
6. The model logic was not separated cleanly from presentation.

Version 3 addresses these problems by separating the system into four layers:

1. **Workbook reference layer** — reads the authoritative assumptions.
2. **Calculation layer** — reproduces the model formulas in Python.
3. **Data layer** — stores all precomputed scenarios and annual results in DuckDB.
4. **Application layer** — serves and visualises results through FastAPI and the web UI.

---

## 2. Objectives

The main objectives were:

- Preserve the authoritative workbook logic.
- Remove Excel from the production calculation workflow.
- Support every sector and every permitted percentile combination.
- Make scenario retrieval fast and deterministic.
- Provide annual outputs from 2023 to 2070.
- Provide sector-specific results rather than only regional totals.
- Retain the original map-focused user experience.
- Highlight Riyadh with a thin neon-orange boundary and glow.
- Provide an interactive GVA S-curve.
- Make the results panel readable without covering the map.
- Preserve transparent glass panels and restrained neon styling.
- Validate the Python implementation against the workbook-derived inputs.

---

## 3. Reference Workbook Methodology

The workbook is loaded with `openpyxl` using cached formula results (`data_only=True`). The Python implementation extracts the assumptions once and uses them to generate deterministic results.

The main reference information comes from the following workbook areas:

- Main Version 3 worksheet
- Sector GVA lookup sheets
- Sector GVA-employment lookup sheets
- `Emp-Pop-Delta-3`
- `Riyadh_GVA_Emp_NACE Sector`
- `Riyadh_OE`
- `Saudi-Non Saudi Assumption`
- `Income Distribution Assumption`

The workbook remains the source of truth for:

- Percentile distributions
- Sector lookup values
- Base GVA
- Productivity
- Population ratios
- Workforce composition
- Income rates
- Consumption assumptions

The Python code does not invent new economic assumptions. It reads the workbook values and applies the model relationships explicitly.

---

## 4. Scenario Dimensions

Version 3 generates scenarios by combining three percentile assumptions.

### 4.1 GVA share percentile

The GVA share percentile uses:

```text
5%, 10%, 15%, ..., 100%
```

This produces 20 possible GVA percentile values.

### 4.2 GVA-employment delta percentile

The GVA-employment delta percentile uses:

```text
0%, 5%, 10%, ..., 100%
```

This produces 21 possible values.

### 4.3 Employment-population delta percentile

The employment-population delta percentile also uses:

```text
0%, 5%, 10%, ..., 100%
```

This produces 21 possible values.

### 4.4 Combinations per sector

The number of combinations per sector is:

```text
20 × 21 × 21 = 8,820
```

With 10 sectors:

```text
8,820 × 10 = 88,200 scenarios
```

Each scenario has 48 annual records:

```text
88,200 × 48 = 4,233,600 annual records
```

---

## 5. Sector Mapping

The workbook uses NACE-style sector groupings. Version 3 maps the application sectors to the corresponding workbook columns and lookup tables.

| Application sector code | Workbook mapping |
| ----------------------- | ---------------- |
| A                       | C                |
| B-E                     | D                |
| F                       | E                |
| G-I                     | F                |
| J                       | G                |
| K                       | H                |
| L                       | I                |
| M_N                     | J                |
| O-Q                     | K                |
| R-U                     | L                |

The application exposes these as user-friendly sector names while retaining the workbook codes internally.

---

## 6. Core Calculation Methodology

### 6.1 Scenario lookup

For each selected sector and percentile combination:

1. The selected GVA percentile is looked up in the sector GVA distribution.
2. The selected GVA-employment percentile is looked up in the sector GVA-employment distribution.
3. The selected employment-population percentile is looked up in the workbook distribution.
4. The selected values are inserted into the sector-specific model assumptions.
5. Annual results are calculated for every year from 2023 through 2070.

The selected sector receives the user-selected percentile values. Other sectors use their baseline workbook percentiles.

### 6.2 GVA share S-curve

The annual GVA share is calculated using a logistic S-curve. The implementation uses the workbook-derived target and the confirmed Version 3 curve parameters:

```text
Base share: 0.0001
Start year: 2023
Steepness: 0.25
Inflection: workbook-derived
```

The S-curve produces a gradual transition from the initial share toward the selected target. This avoids an unrealistic instant jump in sector share.

Conceptually:

```text
share(year) = base + target-adjusted logistic progression
```

The exact implementation is located in [v3_model.py](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/data_pipeline/v3_model.py>).

### 6.3 Annual GVA

For each sector:

```text
sector GVA = sector GVA share × sector base GVA
```

The selected sector result is stored separately so that the user interface can display the selected sector rather than only the sum across all sectors.

The aggregate GVA is also retained in the database for future use.

### 6.4 Annual employment

Employment is calculated using GVA, productivity, and the GVA-employment delta:

```text
employment =
GVA × 1,000,000
÷ productivity
÷ (1 + GVA-employment delta)
```

The calculation is performed for every sector.

The following values are retained:

- Total employment
- Selected sector employment
- Selected sector Saudi employment
- Selected sector non-Saudi employment

### 6.5 Saudi and non-Saudi employment

Each sector is assigned a workforce group:

- Skilled sectors
- Mixed sectors
- Labour-intensive sectors

For each sector:

```text
Saudi jobs = total sector jobs × Saudi workforce share
Non-Saudi jobs = total sector jobs × non-Saudi workforce share
```

The shares are read from the workbook’s Saudi/non-Saudi assumption section.

### 6.6 Population

Population is derived from employment using the Riyadh population-to-employment ratio and the selected employment-population delta:

```text
population =
employment
÷ Riyadh population-to-employment ratio
÷ (1 + employment-population delta)
```

Both aggregate population and selected-sector population are calculated and stored. Population cards were later removed from the UI at the user’s request, but the database fields remain available.

Population in this implementation is an employment-attributed estimate. It should not be interpreted as a directly observed residential population count for each sector.

### 6.7 Income

Sector income is calculated from the sector’s Saudi and non-Saudi employment:

```text
income =
12 × Saudi jobs × Saudi monthly income
+
12 × non-Saudi jobs × non-Saudi monthly income
```

The result is stored in SAR million.

Aggregate income is calculated by summing the sector income values. Selected-sector income is stored separately for the UI.

### 6.8 Consumption

Consumption is derived from income and the workbook marginal propensity to consume:

```text
consumption =
income in SAR × MPC
```

The consumption fields remain in the database, although the Consumption card was removed from the UI at the user’s request.

---

## 7. Sector-Specific Results Correction

The initial result cards displayed aggregate values such as:

- Total GVA
- Total employment
- Total population
- Total income
- Total consumption

This did not match the intended user experience. If the user selected Agriculture, the cards needed to show Agriculture values rather than totals across all sectors.

The implementation was corrected by adding sector-specific annual fields:

- `sector_gva_sar_million`
- `sector_employment_saudi`
- `sector_employment_non_saudi`
- `sector_employment_total`
- `sector_population`
- `sector_income`
- `sector_consumption`

The UI now changes the labels dynamically based on the selected sector. For example:

```text
Agriculture GVA
Agriculture employment
Agriculture Saudi jobs
Agriculture non-Saudi jobs
Agriculture income
```

Population and Consumption were subsequently removed from the visible card grid, while their data remains available in the API and database.

---

## 8. Database Design

DuckDB was selected because it is:

- Embedded
- Fast for analytical queries
- Easy to distribute locally
- Suitable for millions of rows
- Compatible with Python
- Simple to query from FastAPI
- Independent of a separate database server

### 8.1 Scenario master table

The scenario master table stores one row per scenario, including:

- Scenario ID
- Workbook version
- Region
- Sector code
- Sector name
- Workbook column
- GVA percentile
- GVA share result
- GVA-employment percentile
- GVA-employment result
- Employment-population percentile
- Employment-population result
- Generation timestamp

### 8.2 Annual results table

The annual results table stores one row per scenario and year, including:

- Year
- GVA share S-curve
- Aggregate GVA
- Aggregate Saudi employment
- Aggregate non-Saudi employment
- Aggregate employment
- Aggregate population
- Aggregate income
- Aggregate consumption
- Selected-sector GVA
- Selected-sector Saudi employment
- Selected-sector non-Saudi employment
- Selected-sector employment
- Selected-sector population
- Selected-sector income
- Selected-sector consumption

The database was regenerated after the sector-specific fields were added.

Verified output:

```text
Scenario count: 88,200
Annual row count: 4,233,600
```

---

## 9. API Architecture

The FastAPI backend provides:

### `/api/v3/filters`

Returns:

- Available regions
- Available sectors
- Minimum projection year
- Maximum projection year
- Default projection year

### `/api/v3/scenario`

Accepts:

- Sector code
- Region
- GVA percentile
- GVA-employment delta percentile
- Employment-population delta percentile

Returns:

- Scenario metadata
- Workbook results
- Annual aggregate results
- Annual sector-specific results

The endpoint performs a deterministic DuckDB lookup rather than recalculating the entire model for every browser request.

### `/api/boundaries`

Returns the administrative boundary GeoJSON used by the map.

### `/api/health`

Reports API and database availability.

---

## 10. User Interface Methodology

The interface was restored to the map-based Version 3 design rather than retaining a simplified form layout.

### 10.1 Left parameter panel

The left panel includes:

- Region selector
- Sector selector
- GVA percentile slider
- GVA-employment delta slider
- Employment-population delta slider
- Run Projection button

The panel uses:

- Transparent glass styling
- Custom scrolling
- Neon slider thumbs
- Dropdown hover and focus effects
- Animated projection button

### 10.2 Interactive map

MapLibre is used for the map interface. The base imagery is provided by Esri World Imagery tiles.

The map includes:

- Satellite imagery
- Administrative boundaries
- Zoom controls
- Reset control
- Coordinate display
- Riyadh boundary highlight

Riyadh is identified from the boundary GeoJSON using:

```text
NAME_1 = "Ar Riyad"
```

The Riyadh styling uses:

- Thin orange core boundary
- Wider blurred orange glow
- Low-opacity orange fill

### 10.3 Results panel

After analysis:

- The map remains in the center column.
- The results panel occupies a dedicated right column.
- The results panel slides in from the right.
- The map is resized before and after the layout transition.
- The map is refit to Riyadh after the center-column resize.

This prevents the results panel from covering the map.

### 10.4 KPI cards

The cards use restrained neon styling:

- Neon value colors
- Thin borders
- Subtle inner highlight
- Narrow shadow
- No large colored orbs inside the cards

Population and Consumption cards are currently hidden from the UI.

### 10.5 GVA S-curve

The S-curve is rendered with ECharts.

The chart rendering issue was caused by initializing the chart while the results panel was hidden. The corrected sequence is:

1. Open the results layout.
2. Make the results panel visible.
3. Render the chart.
4. Resize the chart after layout calculation.

This ensures the chart receives a valid width and remains visible.

---

## 11. Validation and Testing

Validation was performed at several levels.

### 11.1 Python syntax validation

Syntax checks were run for:

- The V3 calculation engine
- The FastAPI backend

### 11.2 Focused calculation tests

The V3 model tests validate:

- Percentile lookup behavior
- Sector mapping
- S-curve calculation
- Annual output generation

### 11.3 Database validation

The generated database was checked for:

- Scenario count
- Annual row count
- Annual year range
- Sector-specific columns
- Sample Agriculture values

### 11.4 API validation

The API was tested for:

- Filter retrieval
- Scenario retrieval
- Missing scenario handling
- Annual records
- Sector-specific output fields

### 11.5 Browser validation

The browser UI was tested for:

- Map loading
- Satellite layer
- Boundary rendering
- Riyadh orange glow
- Scenario execution
- Right-side results transition
- Sector-specific card labels
- Sector-specific card values
- S-curve visibility
- Closing and reopening results

Agriculture and Construction were both checked after the sector-specific output changes.

---

## 12. Why Python Instead of Excel for Production

Excel remains important, but Python is better suited for the application layer because it provides:

- Reproducible calculations
- Explicit formulas
- Automated testing
- API integration
- Database generation
- Batch scenario creation
- Clear separation of logic and presentation
- Easier maintenance of UI behavior
- Better support for map and browser technologies

The chosen approach is therefore:

```text
Excel = authoritative reference and validation source
Python = production calculation engine
DuckDB = scenario storage and query layer
FastAPI = service layer
HTML/CSS/JavaScript = interactive presentation layer
```

This preserves confidence in the workbook while avoiding dependence on Excel during normal application use.

---

## 13. Current Limitations and Considerations

### 13.1 Workbook dependency for regeneration

The workbook is required when rebuilding the scenario database or changing the model assumptions. It is not required for normal scenario retrieval after the database has been generated.

### 13.2 Population interpretation

Sector population is derived from sector employment. It is not a separately observed population series by sector.

### 13.3 Consumption units

The internal consumption calculation converts income from SAR million into SAR before applying MPC. The current UI card has been removed, but any future display should clearly state whether the value is:

- SAR
- SAR million

### 13.4 Database rebuild time

A full database rebuild is computationally intensive because it generates 88,200 scenarios and 4,233,600 annual rows. It can take a substantial amount of time on a local machine and requires the DuckDB file not to be locked by a running API process.

### 13.5 Map tile availability

The map depends on network access to the Esri imagery tile service. If the tile service is unavailable, boundary and interface functionality can still exist, but satellite imagery may not load.

---

## 14. Main Project Files

### Calculation and data

- [v3_model.py](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/data_pipeline/v3_model.py>) — workbook extraction, calculations, schema, and generation
- [generate_v3_scenarios.py](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/data_pipeline/generate_v3_scenarios.py>) — database generation command
- [v3_scenarios.duckdb](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/data_pipeline/processed/v3_scenarios.duckdb>) — generated scenario database
- [55007720_M14_Project_Grant_Projection_Model.xlsx](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/data_pipeline/raw_excel/55007720_M14_Project_Grant_Projection_Model.xlsx>) — authoritative workbook reference

### API

- [main.py](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/backend_api/main.py>) — FastAPI service and scenario endpoints

### Frontend

- [index.html](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/static_frontend/index.html>) — application structure
- [app.js](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/static_frontend/app.js>) — map, scenario requests, charts, and UI behavior
- [style.css](<C:/Users/INAK165320/WSP O365/Akshay Kumar/Month 10/DIGITAL TWIN/riyadh_digital_twin/static_frontend/style.css>) — glass, neon, responsive, and layout styling

---

## 15. Conclusion

Version 3 transforms the workbook-based projection model into a repeatable analytical application without discarding the workbook’s validated assumptions. The calculation logic is explicit in Python, the full scenario space is precomputed in DuckDB, the API provides deterministic access to results, and the browser interface gives users a visual way to explore sector assumptions and outputs.

The implementation now supports:

- All Version 3 sectors
- All permitted percentile combinations
- Annual projections from 2023 to 2070
- Sector-specific GVA and employment outputs
- Sector-specific Saudi and non-Saudi jobs
- Sector-specific income
- Map-based Riyadh highlighting
- Interactive S-curve visualisation
- Responsive results layout

The application is therefore ready to serve as the operational Version 3 interface, with the original workbook retained as the authoritative validation reference.
