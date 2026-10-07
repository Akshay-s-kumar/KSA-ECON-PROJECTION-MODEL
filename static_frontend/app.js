"use strict";

// ============================================================
// KSA Economic Projection Model - Version 4 frontend
// V3 behaviour + shared comparator population band (4 inputs)
// ============================================================

const state = {
    filters: null,
    scenario: null,
    economy: null,
    map: null,
    boundaries: null,
    chart: null,
    allChart: null
};

const $ = (id) => document.getElementById(id);
const initialView = { center: [45.1, 24.3], zoom: 4.55 };
const BAND_KEYS = ["nuts3_min", "nuts3_max", "nuts2_min", "nuts2_max"];
const LOW_COMPARATOR_COUNT = 10;
const SECTOR_COLORS = ["#ff7438", "#bd8cff", "#55e890", "#67b7ff", "#ffc35a",
                       "#ff87c8", "#55e5f5", "#c4f25a", "#ff6b6b", "#e2e8f0"];
let assumptionUpdateTimeout;
let scenarioRequestVersion = 0;

// ============================================================
// STARTUP
// ============================================================
document.addEventListener("DOMContentLoaded", async () => {
    bindControls();
    initializeMap();
    try {
        state.filters = await request("/api/v4/filters");
        populateFilters(state.filters);
        $("map-status").textContent = "Choose a sector projection or view all-sector GVA";
        await loadBoundaries();
        runScenario();
    } catch (error) {
        showError(error.message);
        $("map-status").textContent = "Unable to load model data";
    }
});

// ============================================================
// CONTROLS
// ============================================================
function bindControls() {
    [["gva-percentile", "gva-value"], ["gva-emp-percentile", "gva-emp-value"], ["emp-pop-percentile", "emp-pop-value"]].forEach(([input, output]) => {
        $(input).addEventListener("input", () => {
            $(output).textContent = `${$(input).value}%`;
            scheduleScenarioUpdate();
        });
        $(output).textContent = `${$(input).value}%`;
    });

    $("all-sector-controls").addEventListener("input", (event) => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || !input.dataset.output) return;
        $(input.dataset.output).textContent = `${input.value}%`;
        scheduleScenarioUpdate();
    });

    // Population band: "change" fires on Enter or when leaving the box, not on every keystroke.
    document.querySelectorAll(".band-input").forEach((input) => {
        input.addEventListener("change", () => {
            if (readBand()) runScenario();
        });
        input.addEventListener("input", () => input.classList.remove("is-invalid"));
    });
    $("band-reset").addEventListener("click", () => {
        setBandInputs(state.filters.default_band);
        if (readBand()) runScenario();
    });

    $("run-button").addEventListener("click", runScenario);
    $("sector-select").addEventListener("change", () => {
        updateSectorControl();
        runScenario();
    });
    $("close-results").addEventListener("click", closeResults);
    $("year-slider").addEventListener("input", updateYear);
    $("zoom-in").addEventListener("click", () => state.map.zoomIn());
    $("zoom-out").addEventListener("click", () => state.map.zoomOut());
    $("reset-map").addEventListener("click", () => state.map.flyTo(initialView));
    window.addEventListener("resize", resizeAllCharts);
}

// ============================================================
// POPULATION BAND
// ============================================================
function bandInputId(key) {
    return `band-${key.replace("_", "-")}`;           // nuts3_min -> band-nuts3-min
}

function setBandInputs(band) {
    BAND_KEYS.forEach((key) => $(bandInputId(key)).value = band[key]);
}

function readBand() {
    const band = {};
    const invalid = new Set();

    BAND_KEYS.forEach((key) => {
        const raw = $(bandInputId(key)).value.trim();
        const value = Number(raw);
        if (raw === "" || !Number.isFinite(value) || value < 0) invalid.add(key);
        band[key] = value;
    });
    if (band.nuts3_min > band.nuts3_max) { invalid.add("nuts3_min"); invalid.add("nuts3_max"); }
    if (band.nuts2_min > band.nuts2_max) { invalid.add("nuts2_min"); invalid.add("nuts2_max"); }

    document.querySelectorAll(".band-input").forEach((input) =>
        input.classList.toggle("is-invalid", invalid.has(input.dataset.key))
    );

    if (invalid.size) {
        setBandStatus("Check the highlighted figures. Minimum must not be higher than maximum.", "error");
        return null;
    }
    return band;
}

function renderComparators(counts) {
    if (!counts) return;
    const lowest = Math.min(counts.gva, counts.gva_emp, counts.emp_pop);
    const text = `GVA ${counts.gva} · GVA–Emp ${counts.gva_emp} · Emp–Pop ${counts.emp_pop} regions pass`;
    if (lowest === 0) setBandStatus(`${text}. Widen the range.`, "error");
    else if (lowest < LOW_COMPARATOR_COUNT) setBandStatus(`${text}. Few regions: percentiles will barely change.`, "warning");
    else setBandStatus(`✓ ${text}`, "ok");
}

function setBandStatus(message, level) {
    const status = $("band-status");
    status.textContent = message;
    status.className = `band-status is-${level}`;
}

function formatBand(band) {
    if (!band) return "—";
    return `NUTS3 ${formatNumber(band.nuts3_min)}–${formatNumber(band.nuts3_max)} · ` +
           `NUTS2 ${formatNumber(band.nuts2_min)}–${formatNumber(band.nuts2_max)}`;
}

// ============================================================
// MAP
// ============================================================
function initializeMap() {
    state.map = new maplibregl.Map({
        container: "map",
        center: initialView.center,
        zoom: initialView.zoom,
        style: {
            version: 8,
            sources: {
                satellite: {
                    type: "raster",
                    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
                    tileSize: 256,
                    attribution: "Tiles © Esri"
                }
            },
            layers: [{
                id: "satellite",
                type: "raster",
                source: "satellite",
                paint: { "raster-opacity": .76, "raster-saturation": -.35, "raster-contrast": .15 }
            }]
        }
    });
    state.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    state.map.on("mousemove", (event) =>
        $("map-coordinate").textContent = `${event.lngLat.lat.toFixed(2)}°N ${event.lngLat.lng.toFixed(2)}°E`
    );
}

async function loadBoundaries() {
    const response = await fetch("/api/boundaries");
    if (!response.ok) throw new Error("Administrative boundary data is unavailable.");
    state.boundaries = await response.json();
    if (state.map.loaded()) addBoundaryLayers();
    else state.map.once("load", addBoundaryLayers);
}

function addBoundaryLayers() {
    if (state.map.getSource("boundaries")) return;
    const riyadhFilter = ["==", ["get", "NAME_1"], "Ar Riyad"];

    state.map.addSource("boundaries", { type: "geojson", data: state.boundaries });
    state.map.addLayer({ id: "boundary-fill", type: "fill", source: "boundaries",
        paint: { "fill-color": "#806cff", "fill-opacity": .08 } });
    state.map.addLayer({ id: "boundary-outline", type: "line", source: "boundaries",
        paint: { "line-color": "#b8a9ff", "line-width": 1.2, "line-opacity": .82 } });
    state.map.addLayer({ id: "riyadh-fill-glow", type: "fill", source: "boundaries", filter: riyadhFilter,
        paint: { "fill-color": "#ff7438", "fill-opacity": 0 } });
    state.map.addLayer({ id: "riyadh-glow", type: "line", source: "boundaries", filter: riyadhFilter,
        paint: { "line-color": "#ff642b", "line-width": 8, "line-opacity": 0, "line-blur": 5 } });
    state.map.addLayer({ id: "riyadh-highlight", type: "line", source: "boundaries", filter: riyadhFilter,
        paint: { "line-color": "#ff9a69", "line-width": 1.2, "line-opacity": 0 } });

    state.map.on("click", "boundary-fill", (event) => {
        const feature = event.features?.[0];
        if (feature) focusFeature(feature);
    });
    state.map.on("mouseenter", "boundary-fill", () => state.map.getCanvas().style.cursor = "pointer");
    state.map.on("mouseleave", "boundary-fill", () => state.map.getCanvas().style.cursor = "");

    if ($("app-shell").classList.contains("results-open")) {
        highlightRiyadh();
        zoomToRiyadh();
    }
}

function highlightRiyadh() {
    if (!state.map.getLayer("riyadh-glow")) return;
    state.map.setPaintProperty("riyadh-fill-glow", "fill-opacity", 0.06);
    state.map.setPaintProperty("riyadh-glow", "line-opacity", 0.7);
    state.map.setPaintProperty("riyadh-highlight", "line-opacity", 1);
}

function zoomToRiyadh() {
    const feature = state.boundaries?.features?.find((item) => item.properties?.NAME_1 === "Ar Riyad");
    if (feature) focusFeature(feature);
}

function focusFeature(feature) {
    const bounds = new maplibregl.LngLatBounds();
    const visit = (coordinates) =>
        Array.isArray(coordinates[0]) ? coordinates.forEach(visit) : bounds.extend(coordinates);
    visit(feature.geometry.coordinates);
    state.map.fitBounds(bounds, {
        padding: { top: 65, right: 55, bottom: 65, left: 55 },
        duration: 1400,
        maxZoom: 5.7
    });
}

// ============================================================
// FILTERS AND ASSUMPTION CONTROLS
// ============================================================
function populateFilters(filters) {
    $("region-select").innerHTML = filters.regions
        .map((item) => `<option value="${item.code}">${item.name}</option>`).join("");
    $("sector-select").innerHTML =
        `<option value="ALL">All sectors — economy total</option>` +
        filters.sectors.map((item) => `<option value="${item.code}">${item.name}</option>`).join("");
    $("sector-select").value = filters.sectors[0]?.code ?? "ALL";
    buildSectorAssumptions(filters.sectors);
    setBandInputs(filters.default_band);
    $("year-slider").min = filters.years.minimum;
    $("year-slider").max = filters.years.maximum;
    $("year-slider").value = filters.years.default;
    $("year-value").textContent = filters.years.default;
    updateSectorControl();
}

function updateSectorControl() {
    window.clearTimeout(assumptionUpdateTimeout);
    scenarioRequestVersion += 1;
    $("run-button").disabled = false;
    const isEconomyOverview = $("sector-select").value === "ALL";
    $("scenario-assumptions").hidden = isEconomyOverview;
    $("all-sector-assumptions").classList.toggle("is-hidden", !isEconomyOverview);
    $("run-button").innerHTML = "Refresh Projection <span>→</span>";
    $("control-note").textContent = isEconomyOverview
        ? "Set assumptions for each sector below. Results update automatically."
        : "Results update automatically when you move a slider or change a population figure.";
    if (isEconomyOverview) $("map-status").textContent = "Loading all-sector projections for Riyadh Region...";
}

function buildSectorAssumptions(sectors) {
    const container = $("all-sector-controls");
    container.replaceChildren(...sectors.map((sector) => {
        const card = document.createElement("article");
        card.className = "sector-assumption-card";
        const heading = document.createElement("h4");
        heading.textContent = sector.name;
        card.append(heading);

        [
            ["gva_percentile", "GVA percentile", 5, 100],
            ["gva_emp_delta_percentile", "GVA–employment delta", 0, 100],
            ["emp_pop_delta_percentile", "Employment–population delta", 0, 100]
        ].forEach(([assumption, labelText, minimum, maximum]) => {
            const inputId = `assumption-${sector.code}-${assumption}`;
            const outputId = `${inputId}-value`;

            const label = document.createElement("div");
            label.className = "range-label";
            const name = document.createElement("label");
            name.htmlFor = inputId;
            name.textContent = labelText;
            const output = document.createElement("output");
            output.id = outputId;
            output.textContent = "50%";
            label.append(name, output);

            const input = document.createElement("input");
            input.id = inputId;
            input.type = "range";
            input.min = minimum;
            input.max = maximum;
            input.step = "5";
            input.value = "50";
            input.dataset.sectorCode = sector.code;
            input.dataset.assumption = assumption;
            input.dataset.output = outputId;

            card.append(label, input);
        });
        return card;
    }));
}

// ============================================================
// SCENARIO REQUESTS
// ============================================================
function scheduleScenarioUpdate() {
    scenarioRequestVersion += 1;
    window.clearTimeout(assumptionUpdateTimeout);
    assumptionUpdateTimeout = window.setTimeout(runScenario, 250);
}

async function runScenario() {
    window.clearTimeout(assumptionUpdateTimeout);
    const band = readBand();
    if (!band) return;                                  // keep previous results on screen

    const requestVersion = ++scenarioRequestVersion;
    $("run-button").disabled = true;
    hideError();

    try {
        if ($("sector-select").value === "ALL") {
            const sectors = state.filters.sectors.map((sector) => {
                const assumptions = { sector_code: sector.code };
                $("all-sector-controls")
                    .querySelectorAll(`input[data-sector-code="${sector.code}"]`)
                    .forEach((input) => assumptions[input.dataset.assumption] = Number(input.value) / 100);
                return assumptions;
            });

            const economy = await request("/api/v4/economy/scenario", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ region: $("region-select").value, band, sectors })
            });
            if (requestVersion !== scenarioRequestVersion) return;

            state.economy = economy;
            state.scenario = null;
            renderComparators(economy.comparators);
            $("economy-map-card").classList.remove("is-hidden");
            $("results-panel").classList.add("economy-overview");
            displayResults(renderEconomyOverview);
            return;
        }

        const scenario = await request("/api/v4/scenario", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                sector_code: $("sector-select").value,
                region: "Riyadh",
                band,
                gva_percentile: Number($("gva-percentile").value) / 100,
                gva_emp_delta_percentile: Number($("gva-emp-percentile").value) / 100,
                emp_pop_delta_percentile: Number($("emp-pop-percentile").value) / 100
            })
        });
        if (requestVersion !== scenarioRequestVersion) return;

        state.economy = null;
        state.scenario = scenario;
        renderComparators(scenario.comparators);
        $("economy-map-card").classList.add("is-hidden");
        $("results-panel").classList.remove("economy-overview");
        displayResults(renderScenario);
    } catch (error) {
        if (requestVersion !== scenarioRequestVersion) return;
        if (error.status === 422) setBandStatus(error.message, "error");   // band problem: keep old results
        else showError(error.message);
    } finally {
        if (requestVersion === scenarioRequestVersion) $("run-button").disabled = false;
    }
}

// ============================================================
// RESULTS PANEL LAYOUT
// ============================================================
function displayResults(render) {
    if ($("app-shell").classList.contains("results-open")
        && !$("results-panel").classList.contains("is-hidden")) {
        render();
        return;
    }
    showResults(render);
}

function showResults(render) {
    $("app-shell").classList.add("results-open");
    window.requestAnimationFrame(() => {
        $("results-panel").classList.remove("is-hidden");
        state.map.resize();
        render();
        highlightRiyadh();
        window.setTimeout(() => {
            state.map.resize();
            resizeAllCharts();
            zoomToRiyadh();
        }, 700);
    });
}

function closeResults() {
    $("app-shell").classList.remove("results-open");
    $("results-panel").classList.add("is-hidden");
    state.map.resize();
    window.setTimeout(() => state.map.resize(), 700);
}

// ============================================================
// SINGLE-SECTOR VIEW
// ============================================================
function renderScenario() {
    const { scenario, annual_results: annual, band } = state.scenario;
    const selectedYear = Number($("year-slider").value);

    $("results-panel").classList.remove("economy-overview");
    $("economy-map-card").classList.add("is-hidden");
    $("sector-breakdown").classList.add("is-hidden");
    $("map-status").textContent = `Showing ${scenario.sector_name} projection`;
    $("scenario-title").textContent = scenario.sector_name;

    const sectorLabel = scenario.sector_name.replace(/,.*$/, "");
    [
        ["label-gva", `${sectorLabel} GVA`],
        ["label-employment", `${sectorLabel} employment`],
        ["label-saudi", `${sectorLabel} Saudi jobs`],
        ["label-non-saudi", `${sectorLabel} non-Saudi jobs`],
        ["label-income", `${sectorLabel} income`]
    ].forEach(([id, value]) => $(id).textContent = value);

    $("year-slider").min = annual[0].year;
    $("year-slider").max = annual[annual.length - 1].year;
    $("year-slider").value = annual.some((row) => row.year === selectedYear) ? selectedYear : annual[0].year;

    $("scenario-details").innerHTML = [
        ["Scenario ID", scenario.scenario_id],
        ["Workbook", scenario.workbook_version],
        ["Comparator band", formatBand(band)],
        ["GVA result", formatPercent(scenario.gva_share_result)],
        ["GVA-Employment delta", formatPercent(scenario.gva_emp_delta_result)],
        ["Employment-Population delta", formatPercent(scenario.emp_pop_delta_result)]
    ].map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");

    $("chart-title").textContent = "GVA Share S-curve";
    $("chart-range").textContent = `${annual[0].year}–${annual[annual.length - 1].year}`;

    const years = annual.map((row) => row.year);
    state.chart = state.chart || echarts.init($("gva-chart"));
    state.chart.setOption(sCurveOption(
        years,
        annual.map((row) => row.gva_share_s_curve),
        Number($("year-slider").value)
    ), true);
    requestAnimationFrame(() => state.chart.resize());
    updateYear();
}

// ============================================================
// ALL-SECTORS VIEW
// ============================================================
function renderEconomyOverview() {
    const annual = state.economy.annual_results;
    const selectedYear = Number($("year-slider").value);

    $("results-panel").classList.add("economy-overview");
    $("sector-breakdown").classList.remove("is-hidden");
    $("scenario-title").textContent = "Riyadh Region · All Sectors";
    $("map-status").textContent = "Showing Riyadh Region and all sector projections";

    $("year-slider").min = annual[0].year;
    $("year-slider").max = annual[annual.length - 1].year;
    $("year-slider").value = annual.some((row) => row.year === selectedYear) ? selectedYear : annual[0].year;

    buildSectorBlocks();
    updateYear();
}

function buildSectorBlocks() {
    const annual = state.economy.annual_results;
    const years = annual.map((row) => row.year);
    const container = $("sector-breakdown-list");

    container.replaceChildren(...annual[0].sectors.map((sector) => {
        const block = document.createElement("article");
        block.className = "sector-block";
        block.dataset.sectorCode = sector.sector_code;
        block.innerHTML = `
            <h4></h4>
            <div class="sector-metrics">
                <div><span>Employment</span><strong data-field="employment_total">—</strong><small>total jobs</small></div>
                <div><span>Saudi jobs</span><strong data-field="employment_saudi">—</strong><small>jobs</small></div>
                <div><span>Non-Saudi jobs</span><strong data-field="employment_non_saudi">—</strong><small>jobs</small></div>
                <div><span>GVA</span><strong data-field="gva_sar_million">—</strong><small>SAR million</small></div>
            </div>`;
        block.querySelector("h4").textContent = sector.sector_name;
        return block;
    }));

    renderAllSectorChart(years, annual);
}

// One chart: GVA share S-curve for every sector
function renderAllSectorChart(years, annual) {
    const selectedYear = Number($("year-slider").value);
    const series = annual[0].sectors.map((sector, index) => {
        const color = SECTOR_COLORS[index % SECTOR_COLORS.length];
        return {
            name: shortSectorName(sector.sector_name),
            type: "line",
            smooth: true,
            symbol: "none",
            emphasis: { focus: "series" },
            lineStyle: { width: 2, color },
            itemStyle: { color },
            data: annual.map((row) =>
                row.sectors.find((item) => item.sector_code === sector.sector_code)?.gva_share_s_curve ?? null
            )
        };
    });

    // Year marker sits on the first series; moveYearMarker updates it
    series[0].markLine = {
        symbol: "none",
        silent: true,
        animation: false,
        lineStyle: { color: "#ffffff", type: "dashed", width: 1 },
        label: { formatter: String(selectedYear), color: "#ffffff", fontSize: 10 },
        data: [{ xAxis: years.indexOf(selectedYear) }]
    };

    $("all-chart-range").textContent = `${years[0]}–${years[years.length - 1]}`;
    state.allChart = state.allChart || echarts.init($("all-sector-chart"));
    state.allChart.setOption({
        animation: true,
        tooltip: { trigger: "axis", confine: true, valueFormatter: formatPercent,
                   textStyle: { fontSize: 10 } },
        legend: { type: "scroll", bottom: 0, textStyle: { color: "#c3ccdb", fontSize: 9 },
                  pageTextStyle: { color: "#c3ccdb" }, itemWidth: 14, itemHeight: 8 },
        grid: { left: 46, right: 16, top: 18, bottom: 62 },
        xAxis: { type: "category", data: years, axisLabel: { color: "#aeb8ca" } },
        yAxis: { type: "value", axisLabel: { color: "#aeb8ca", formatter: (value) => `${(value * 100).toFixed(1)}%` },
                 splitLine: { lineStyle: { color: "rgba(183,197,224,.12)" } } },
        series
    }, true);
    requestAnimationFrame(() => state.allChart.resize());
}

function shortSectorName(name) {
    return String(name).replace(/,.*$/, "");
}

// ============================================================
// YEAR SLIDER
// ============================================================
function updateYear() {
    const year = Number($("year-slider").value);
    $("year-value").textContent = year;

    if (state.economy) {
        const row = state.economy.annual_results.find((item) => item.year === year);
        if (!row) return;

        $("economy-population").textContent = formatNumber(row.population);
        $("map-economy-year").textContent = year;
        $("map-total-population").textContent = formatNumber(row.population);
        $("map-total-employment").textContent = formatNumber(row.employment_total);
        $("map-total-gva").textContent = formatNumber(row.total_gva_sar_million);

        const years = state.economy.annual_results.map((item) => item.year);
        row.sectors.forEach((sector) => {
            const block = document.querySelector(`.sector-block[data-sector-code="${sector.sector_code}"]`);
            if (!block) return;
            block.querySelectorAll("[data-field]").forEach((cell) => {
                cell.textContent = formatNumber(sector[cell.dataset.field]);
            });
        });
        moveYearMarker(state.allChart, years, year);
        return;
    }

    const row = state.scenario?.annual_results.find((item) => item.year === year);
    if (!row) return;
    [
        ["kpi-gva-share", formatPercent(row.gva_share_s_curve)],
        ["kpi-gva", formatNumber(row.sector_gva_sar_million)],
        ["kpi-employment", formatNumber(row.sector_employment_total)],
        ["kpi-saudi", formatNumber(row.sector_employment_saudi)],
        ["kpi-non-saudi", formatNumber(row.sector_employment_non_saudi)],
        ["kpi-income", formatNumber(row.sector_income)]
    ].forEach(([id, value]) => $(id).textContent = value);
    moveYearMarker(state.chart, state.scenario.annual_results.map((item) => item.year), year);
}

// ============================================================
// CHART HELPERS
// ============================================================
function sCurveOption(years, data, selectedYear) {
    return {
        animation: true,
        tooltip: { trigger: "axis", valueFormatter: formatPercent },
        grid: { left: 42, right: 16, top: 18, bottom: 28 },
        xAxis: { type: "category", data: years },
        yAxis: { type: "value", axisLabel: { formatter: (value) => `${(value * 100).toFixed(1)}%` } },
        series: [{
            type: "line",
            smooth: true,
            symbol: "none",
            data,
            lineStyle: { width: 3, color: "#ff7438" },
            areaStyle: { color: "rgba(255,116,56,.16)" },
            markLine: {
                symbol: "none",
                silent: true,
                animation: false,
                lineStyle: { color: "#ffffff", type: "dashed", width: 1 },
                label: { formatter: String(selectedYear), color: "#ffffff", fontSize: 10 },
                data: [{ xAxis: years.indexOf(selectedYear) }]
            }
        }]
    };
}

function moveYearMarker(chart, years, year) {
    if (!chart) return;
    chart.setOption({
        series: [{ markLine: { label: { formatter: String(year) }, data: [{ xAxis: years.indexOf(year) }] } }]
    });
}

function resizeAllCharts() {
    state.chart?.resize();
    state.allChart?.resize();
}

// ============================================================
// UTILITIES
// ============================================================
async function request(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok) {
        const detail = Array.isArray(payload.detail) ? payload.detail.map((d) => d.msg).join("; ") : payload.detail;
        const error = new Error(detail || "The request failed.");
        error.status = response.status;
        throw error;
    }
    return payload;
}

function formatNumber(value) {
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function formatPercent(value) {
    return `${(Number(value) * 100).toFixed(2)}%`;
}

function showError(message) {
    $("error-message").textContent = message;
    $("error-message").classList.remove("is-hidden");
}

function hideError() {
    $("error-message").classList.add("is-hidden");
}
