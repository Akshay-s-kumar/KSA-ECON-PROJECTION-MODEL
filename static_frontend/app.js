"use strict";

const state = { filters: null, scenario: null, map: null, boundaries: null, chart: null };
const $ = (id) => document.getElementById(id);
const initialView = { center: [45.1, 24.3], zoom: 4.55 };

document.addEventListener("DOMContentLoaded", async () => {
    bindControls();
    initializeMap();
    try {
        state.filters = await request("/api/v3/filters");
        populateFilters(state.filters);
        $("map-status").textContent = "Select a sector and assumptions to begin";
        await loadBoundaries();
    } catch (error) { showError(error.message); $("map-status").textContent = "Unable to load model data"; }
});

function bindControls() {
    [["gva-percentile", "gva-value"], ["gva-emp-percentile", "gva-emp-value"], ["emp-pop-percentile", "emp-pop-value"]].forEach(([input, output]) => {
        $(input).addEventListener("input", () => $(output).textContent = `${$(input).value}%`);
        $(output).textContent = `${$(input).value}%`;
    });
    $("run-button").addEventListener("click", runScenario);
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
    $("sector-select").innerHTML = filters.sectors.map((item) => `<option value="${item.code}">${item.name}</option>`).join("");
    $("year-slider").min = filters.years.minimum; $("year-slider").max = filters.years.maximum; $("year-slider").value = filters.years.default;
    $("year-value").textContent = filters.years.default;
}

async function runScenario() {
    $("run-button").disabled = true; hideError();
    try {
        const params = new URLSearchParams({
            sector_code: $("sector-select").value, region: "Riyadh",
            gva_percentile: (Number($("gva-percentile").value) / 100).toFixed(2),
            gva_emp_delta_percentile: (Number($("gva-emp-percentile").value) / 100).toFixed(2),
            emp_pop_delta_percentile: (Number($("emp-pop-percentile").value) / 100).toFixed(2)
        });
        state.scenario = await request(`/api/v3/scenario?${params}`);
        $("app-shell").classList.add("results-open");
        window.requestAnimationFrame(() => {
            $("results-panel").classList.remove("is-hidden");
            state.map.resize();
            renderScenario();
            highlightRiyadh();
            window.setTimeout(() => {
                state.map.resize();
                zoomToRiyadh();
            }, 700);
        });
    } catch (error) { showError(error.message); } finally { $("run-button").disabled = false; }
}

function closeResults() {
    $("app-shell").classList.remove("results-open");
    $("results-panel").classList.add("is-hidden");
    state.map.resize();
    window.setTimeout(() => state.map.resize(), 700);
}

function renderScenario() {
    const { scenario, annual_results: annual } = state.scenario;
    $("scenario-title").textContent = scenario.sector_name;
    const sectorLabel = scenario.sector_name.replace(/,.*$/, "");
    [["label-gva", `${sectorLabel} GVA`], ["label-employment", `${sectorLabel} employment`],
        ["label-saudi", `${sectorLabel} Saudi jobs`],
        ["label-non-saudi", `${sectorLabel} non-Saudi jobs`], ["label-income", `${sectorLabel} income`],
    ]
        .forEach(([id, value]) => $(id).textContent = value);
    $("year-slider").value = annual[0].year;
    $("scenario-details").innerHTML = [
        ["Scenario ID", scenario.scenario_id], ["Workbook", scenario.workbook_version],
        ["GVA result", formatPercent(scenario.gva_share_result)],
        ["GVA-Employment delta", formatPercent(scenario.gva_emp_delta_result)],
        ["Employment-Population delta", formatPercent(scenario.emp_pop_delta_result)]
    ].map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");
    state.chart = state.chart || echarts.init($("gva-chart"));
    state.chart.setOption({ animation: true, tooltip: { trigger: "axis", valueFormatter: formatPercent },
        grid: { left: 42, right: 16, top: 18, bottom: 28 }, xAxis: { type: "category", data: annual.map((row) => row.year) },
        yAxis: { type: "value", axisLabel: { formatter: (value) => `${(value * 100).toFixed(1)}%` } },
        series: [{ type: "line", smooth: true, symbol: "none", data: annual.map((row) => row.gva_share_s_curve), lineStyle: { width: 3, color: "#ff7438" }, areaStyle: { color: "rgba(255,116,56,.16)" } }] });
    requestAnimationFrame(() => state.chart.resize());
    updateYear();
}

function updateYear() {
    $("year-value").textContent = $("year-slider").value;
    const row = state.scenario?.annual_results.find((item) => item.year === Number($("year-slider").value));
    if (!row) return;
    [["kpi-gva-share", formatPercent(row.gva_share_s_curve)], ["kpi-gva", formatNumber(row.sector_gva_sar_million)], ["kpi-employment", formatNumber(row.sector_employment_total)], ["kpi-saudi", formatNumber(row.sector_employment_saudi)], ["kpi-non-saudi", formatNumber(row.sector_employment_non_saudi)], ["kpi-income", formatNumber(row.sector_income)]].forEach(([id, value]) => $(id).textContent = value);
}

async function request(url) { const response = await fetch(url); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || "The request failed."); return payload; }
function formatNumber(value) { return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 }); }
function formatPercent(value) { return `${(Number(value) * 100).toFixed(2)}%`; }
function showError(message) { $("error-message").textContent = message; $("error-message").classList.remove("is-hidden"); }
function hideError() { $("error-message").classList.add("is-hidden"); }
