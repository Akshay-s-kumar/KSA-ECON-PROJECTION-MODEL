"use strict";

const state = { filters: null, scenario: null, economy: null, map: null, boundaries: null, chart: null };
const $ = (id) => document.getElementById(id);
const initialView = { center: [45.1, 24.3], zoom: 4.55 };
let assumptionUpdateTimeout;
let scenarioRequestVersion = 0;

document.addEventListener("DOMContentLoaded", async () => {
    bindControls();
    initializeMap();
    try {
        state.filters = await request("/api/v3/filters");
        populateFilters(state.filters);
        $("map-status").textContent = "Choose a sector projection or view all-sector GVA";
        await loadBoundaries();
    } catch (error) { showError(error.message); $("map-status").textContent = "Unable to load model data"; }
});

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
    $("run-button").addEventListener("click", runScenario);
    $("sector-select").addEventListener("change", () => {
        updateSectorControl();
        if ($("sector-select").value === "ALL") runScenario();
    });
    $("close-results").addEventListener("click", closeResults);
    $("year-slider").addEventListener("input", updateYear);
    $("zoom-in").addEventListener("click", () => state.map.zoomIn());
    $("zoom-out").addEventListener("click", () => state.map.zoomOut());
    $("reset-map").addEventListener("click", () => state.map.flyTo(initialView));
}

function initializeMap() {
    state.map = new maplibregl.Map({
        container: "map", center: initialView.center, zoom: initialView.zoom,
        style: { version: 8, sources: { satellite: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, attribution: "Tiles © Esri" } },
            layers: [{ id: "satellite", type: "raster", source: "satellite", paint: { "raster-opacity": .76, "raster-saturation": -.35, "raster-contrast": .15 } }] }
    });
    state.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    state.map.on("mousemove", (event) => $("map-coordinate").textContent = `${event.lngLat.lat.toFixed(2)}°N ${event.lngLat.lng.toFixed(2)}°E`);
}

async function loadBoundaries() {
    const response = await fetch("/api/boundaries");
    if (!response.ok) throw new Error("Administrative boundary data is unavailable.");
    state.boundaries = await response.json();
    state.map.on("load", () => {
        state.map.addSource("boundaries", { type: "geojson", data: state.boundaries });
        state.map.addLayer({ id: "boundary-fill", type: "fill", source: "boundaries", paint: { "fill-color": "#806cff", "fill-opacity": .08 } });
        state.map.addLayer({ id: "boundary-outline", type: "line", source: "boundaries", paint: { "line-color": "#b8a9ff", "line-width": 1.2, "line-opacity": .82 } });
        state.map.addLayer({ id: "riyadh-fill-glow", type: "fill", source: "boundaries",
            filter: ["==", ["get", "NAME_1"], "Ar Riyad"],
            paint: { "fill-color": "#ff7438", "fill-opacity": 0 } });
        state.map.addLayer({ id: "riyadh-glow", type: "line", source: "boundaries",
            filter: ["==", ["get", "NAME_1"], "Ar Riyad"],
            paint: { "line-color": "#ff642b", "line-width": 8, "line-opacity": 0, "line-blur": 5 } });
        state.map.addLayer({ id: "riyadh-highlight", type: "line", source: "boundaries",
            filter: ["==", ["get", "NAME_1"], "Ar Riyad"],
            paint: { "line-color": "#ff9a69", "line-width": 1.2, "line-opacity": 0 } });
        state.map.on("click", "boundary-fill", (event) => {
            const feature = event.features?.[0];
            if (feature) focusFeature(feature);
        });
        state.map.on("mouseenter", "boundary-fill", () => state.map.getCanvas().style.cursor = "pointer");
        state.map.on("mouseleave", "boundary-fill", () => state.map.getCanvas().style.cursor = "");
        if ($("sector-select").value === "ALL") {
            highlightRiyadh();
            zoomToRiyadh();
        }
    });
}

function highlightRiyadh() {
    if (!state.map.getLayer("riyadh-glow")) return;
    state.map.setPaintProperty("riyadh-fill-glow", "fill-opacity", 0.06);
    state.map.setPaintProperty("riyadh-glow", "line-opacity", 0.7);
    state.map.setPaintProperty("riyadh-highlight", "line-opacity", 1);
}

function zoomToRiyadh() {
    const feature = state.boundaries?.features?.find(
        (item) => item.properties?.NAME_1 === "Ar Riyad"
    );
    if (feature) {
        focusFeature(feature);
    }
}

function focusFeature(feature) {
    const bounds = new maplibregl.LngLatBounds();
    const visit = (coordinates) => Array.isArray(coordinates[0]) ? coordinates.forEach(visit) : bounds.extend(coordinates);
    visit(feature.geometry.coordinates);
    state.map.fitBounds(bounds, {
        padding: { top: 65, right: 55, bottom: 65, left: 55 },
        duration: 1400,
        maxZoom: 5.7
    });
}

function populateFilters(filters) {
    $("region-select").innerHTML = filters.regions.map((item) => `<option value="${item.code}">${item.name}</option>`).join("");
    $("sector-select").innerHTML = `<option value="ALL">All sectors — economy total</option>${filters.sectors.map((item) => `<option value="${item.code}">${item.name}</option>`).join("")}`;
    $("sector-select").value = filters.sectors[0]?.code ?? "ALL";
    buildSectorAssumptions(filters.sectors);
    $("year-slider").min = filters.years.minimum; $("year-slider").max = filters.years.maximum; $("year-slider").value = filters.years.default;
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
    $("run-button").innerHTML = isEconomyOverview
        ? "Update All-sector Projection <span>→</span>"
        : "Run Projection <span>→</span>";
    $("control-note").textContent = isEconomyOverview
        ? "Set assumptions for each sector below. Results and the Riyadh map update automatically."
        : "Results update automatically when you adjust these assumptions. You can also use Run Projection to refresh.";
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

function scheduleScenarioUpdate() {
    scenarioRequestVersion += 1;
    $("run-button").disabled = false;
    window.clearTimeout(assumptionUpdateTimeout);
    assumptionUpdateTimeout = window.setTimeout(() => {
        runScenario();
    }, 250);
}

async function runScenario() {
    window.clearTimeout(assumptionUpdateTimeout);
    const requestVersion = ++scenarioRequestVersion;
    $("run-button").disabled = true;
    hideError();
    try {
        if ($("sector-select").value === "ALL") {
            const sectors = state.filters.sectors.map((sector) => {
                const assumptions = {
                    sector_code: sector.code,
                    gva_percentile: 0,
                    gva_emp_delta_percentile: 0,
                    emp_pop_delta_percentile: 0,
                };
                $("all-sector-controls")
                    .querySelectorAll(`input[data-sector-code="${sector.code}"]`)
                    .forEach((input) => {
                        assumptions[input.dataset.assumption] = Number(input.value) / 100;
                    });
                return assumptions;
            });
            const economy = await request("/api/v3/economy/scenario", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    region: $("region-select").value,
                    sectors,
                }),
            });
            if (requestVersion !== scenarioRequestVersion) return;
            state.economy = economy;
            state.scenario = null;
            $("economy-map-card").classList.remove("is-hidden");
            $("results-panel").classList.add("economy-overview");
            displayResults(renderEconomyOverview);
            return;
        }

        const params = new URLSearchParams({
            sector_code: $("sector-select").value, region: "Riyadh",
            gva_percentile: (Number($("gva-percentile").value) / 100).toFixed(2),
            gva_emp_delta_percentile: (Number($("gva-emp-percentile").value) / 100).toFixed(2),
            emp_pop_delta_percentile: (Number($("emp-pop-percentile").value) / 100).toFixed(2)
        });
        const scenario = await request(`/api/v3/scenario?${params}`);
        if (requestVersion !== scenarioRequestVersion) return;
        state.economy = null;
        state.scenario = scenario;
        $("economy-map-card").classList.add("is-hidden");
        $("results-panel").classList.remove("economy-overview");
        displayResults(renderScenario);
    } catch (error) {
        if (requestVersion === scenarioRequestVersion) showError(error.message);
    } finally {
        if (requestVersion === scenarioRequestVersion) $("run-button").disabled = false;
    }
}

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

function renderScenario() {
    const { scenario, annual_results: annual } = state.scenario;
    const selectedYear = Number($("year-slider").value);
    $("results-panel").classList.remove("economy-overview");
    $("economy-map-card").classList.add("is-hidden");
    $("sector-breakdown").classList.add("is-hidden");
    $("map-status").textContent = `Showing ${scenario.sector_name} projection`;
    $("scenario-title").textContent = scenario.sector_name;
    const sectorLabel = scenario.sector_name.replace(/,.*$/, "");
    [["label-gva", `${sectorLabel} GVA`], ["label-employment", `${sectorLabel} employment`],
        ["label-saudi", `${sectorLabel} Saudi jobs`],
        ["label-non-saudi", `${sectorLabel} non-Saudi jobs`], ["label-income", `${sectorLabel} income`],
    ]
        .forEach(([id, value]) => $(id).textContent = value);
    $("year-slider").min = annual[0].year;
    $("year-slider").max = annual[annual.length - 1].year;
    $("year-slider").value = annual.some((row) => row.year === selectedYear)
        ? selectedYear
        : annual[0].year;
    $("scenario-details").innerHTML = [
        ["Scenario ID", scenario.scenario_id], ["Workbook", scenario.workbook_version],
        ["GVA result", formatPercent(scenario.gva_share_result)],
        ["GVA-Employment delta", formatPercent(scenario.gva_emp_delta_result)],
        ["Employment-Population delta", formatPercent(scenario.emp_pop_delta_result)]
    ].map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");
    $("chart-title").textContent = "GVA Share S-curve";
    $("chart-range").textContent = `${annual[0].year}–${annual[annual.length - 1].year}`;
    state.chart = state.chart || echarts.init($("gva-chart"));
    state.chart.setOption({ animation: true, tooltip: { trigger: "axis", valueFormatter: formatPercent },
        grid: { left: 42, right: 16, top: 18, bottom: 28 }, xAxis: { type: "category", data: annual.map((row) => row.year) },
        yAxis: { type: "value", axisLabel: { formatter: (value) => `${(value * 100).toFixed(1)}%` } },
        series: [{ type: "line", smooth: true, symbol: "none", data: annual.map((row) => row.gva_share_s_curve), lineStyle: { width: 3, color: "#ff7438" }, areaStyle: { color: "rgba(255,116,56,.16)" } }] });
    requestAnimationFrame(() => state.chart.resize());
    updateYear();
}

function renderEconomyOverview() {
    const annual = state.economy.annual_results;
    const selectedYear = Number($("year-slider").value);
    $("results-panel").classList.add("economy-overview");
    $("sector-breakdown").classList.remove("is-hidden");
    $("scenario-title").textContent = "Riyadh Region · All Sectors";
    $("map-status").textContent = "Showing Riyadh Region and all sector projections";
    $("year-slider").min = annual[0].year;
    $("year-slider").max = annual[annual.length - 1].year;
    $("year-slider").value = annual.some((row) => row.year === selectedYear)
        ? selectedYear
        : annual[0].year;
    updateYear();
}

function updateYear() {
    $("year-value").textContent = $("year-slider").value;
    if (state.economy) {
        const year = Number($("year-slider").value);
        const row = state.economy.annual_results.find((item) => item.year === year);
        if (!row) return;
        $("map-economy-value").textContent = formatNumber(row.total_gva_sar_million);
        $("map-economy-year").textContent = year;
        renderSectorBreakdown(row.sectors);
        return;
    }
    const row = state.scenario?.annual_results.find((item) => item.year === Number($("year-slider").value));
    if (!row) return;
    [["kpi-gva-share", formatPercent(row.gva_share_s_curve)], ["kpi-gva", formatNumber(row.sector_gva_sar_million)], ["kpi-employment", formatNumber(row.sector_employment_total)], ["kpi-saudi", formatNumber(row.sector_employment_saudi)], ["kpi-non-saudi", formatNumber(row.sector_employment_non_saudi)], ["kpi-income", formatNumber(row.sector_income)]].forEach(([id, value]) => $(id).textContent = value);
}

function renderSectorBreakdown(sectors) {
    const container = $("sector-breakdown-list");
    const maximumGva = Math.max(...sectors.map((sector) => sector.gva_sar_million), 0);
    container.replaceChildren(...sectors.map((sector) => {
        const item = document.createElement("article");
        item.className = "sector-breakdown-item";

        const details = document.createElement("div");
        details.className = "sector-breakdown-details";

        const name = document.createElement("strong");
        name.textContent = sector.sector_name;
        const value = document.createElement("span");
        value.textContent = formatNumber(sector.gva_sar_million);
        details.append(name, value);

        const track = document.createElement("div");
        track.className = "sector-breakdown-track";
        track.setAttribute("aria-hidden", "true");
        const bar = document.createElement("span");
        bar.style.width = `${maximumGva > 0 ? sector.gva_sar_million / maximumGva * 100 : 0}%`;
        track.append(bar);

        const share = document.createElement("small");
        share.textContent = `${formatNumber(sector.share_percent)}% of total GVA`;
        item.append(details, track, share);
        return item;
    }));
}

async function request(url, options = {}) { const response = await fetch(url, options); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || "The request failed."); return payload; }
function formatNumber(value) { return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 }); }
function formatPercent(value) { return `${(Number(value) * 100).toFixed(2)}%`; }
function showError(message) { $("error-message").textContent = message; $("error-message").classList.remove("is-hidden"); }
function hideError() { $("error-message").classList.add("is-hidden"); }
