"use strict";

/* ==========================================================
   KSA ECONOMIC PROJECTION MODEL
   static_frontend/app.js
   ========================================================== */


/* ==========================================================
   1. APPLICATION CONFIGURATION
   ========================================================== */

const CONFIG = {
    apiBaseUrl: "",

    initialCenter: [45.1, 24.3],
    initialZoom: 4.55,

    initialBounds: [
        [34.2, 15.2],
        [56.7, 33.5]
    ],

    initialBoundaryColor: "#a591ff",
    selectedBoundaryColor: "#ff8a47",

    mapAnimationDuration: 2200,

    satelliteTiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/" +
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ],

    satelliteAttribution:
        "Tiles © Esri",

    sourceId: "saudi-boundaries-source",

    layerIds: {
        initialGlow: "saudi-boundaries-glow",
        initialOutline: "saudi-boundaries-outline",
        selectedFill: "riyadh-selected-fill",
        selectedOuterGlow: "riyadh-selected-outer-glow",
        selectedGlow: "riyadh-selected-glow",
        selectedOutline: "riyadh-selected-outline"
    }
};


/* ==========================================================
   2. APPLICATION STATE
   ========================================================== */

const state = {
    map: null,
    mapLoaded: false,
    boundaries: null,
    riyadhFeature: null,

    trendChart: null,
    radarChart: null,

    analysisRunning: false,
    resultsVisible: false
};


/* ==========================================================
   3. DOM REFERENCES
   ========================================================== */

const elements = {
    application: document.getElementById("application"),

    regionSelect: document.getElementById("region-select"),
    sectorSelect: document.getElementById("sector-select"),
    metricSelect: document.getElementById("metric-select"),

    yearRange: document.getElementById("year-range"),
    selectedYear: document.getElementById("selected-year"),

    timePeriodRange:
        document.getElementById("time-period-range"),

    runButton:
        document.getElementById("run-analysis-button"),

    closeResultsButton:
        document.getElementById("close-results-button"),

    resultsPanel:
        document.getElementById("results-panel"),

    resultsSelectionSummary:
        document.getElementById("results-selection-summary"),

    mapStatus:
        document.getElementById("map-status"),

    selectedGeographyHeading:
        document.getElementById(
            "selected-geography-heading"
        ),

    mapLoadingOverlay:
        document.getElementById("map-loading-overlay"),

    mapErrorMessage:
        document.getElementById("map-error-message"),

    kpiGva:
        document.getElementById("kpi-gva"),

    kpiBaseGva:
        document.getElementById("kpi-base-gva"),

    kpiBaseGvaYear:
        document.getElementById("kpi-base-gva-year"),

    kpiSector:
        document.getElementById("kpi-sector"),

    kpiShare:
        document.getElementById("kpi-share"),

    kpiGrowth:
        document.getElementById("kpi-growth"),

    kpiYear:
        document.getElementById("kpi-year"),

    trendHighlightValue:
        document.getElementById(
            "trend-highlight-value"
        ),

    trendChartElement:
        document.getElementById("gva-trend-chart"),

    radarChartElement:
        document.getElementById("sector-radar-chart"),

    sectorDistributionSubtitle:
        document.getElementById(
            "sector-distribution-subtitle"
        ),

    sectorResultsTableBody:
        document.getElementById(
            "sector-results-table-body"
        )
};


/* ==========================================================
   4. INITIALIZATION
   ========================================================== */

document.addEventListener(
    "DOMContentLoaded",
    initializeApplication
);


async function initializeApplication() {
    state.resultsVisible = false;
    elements.application.classList.remove("results-visible");
    elements.resultsPanel.setAttribute("aria-hidden", "true");

    validateRequiredLibraries();
    bindInterfaceEvents();
    initializeCharts();
    initializeMap();
    updateYearDisplay();
}


function validateRequiredLibraries() {
    if (typeof maplibregl === "undefined") {
        throw new Error(
            "MapLibre GL JS was not loaded. " +
            "Check the MapLibre script in index.html."
        );
    }

    if (typeof echarts === "undefined") {
        throw new Error(
            "Apache ECharts was not loaded. " +
            "Check the ECharts script in index.html."
        );
    }
}


/* ==========================================================
   5. INTERFACE EVENTS
   ========================================================== */

function bindInterfaceEvents() {
    elements.yearRange.addEventListener(
        "input",
        updateYearDisplay
    );

    elements.runButton.addEventListener(
        "click",
        runAnalysis
    );

    elements.closeResultsButton.addEventListener(
        "click",
        closeResultsPanel
    );

    window.addEventListener(
        "resize",
        debounce(handleWindowResize, 140)
    );
}


function updateYearDisplay() {
    elements.selectedYear.textContent =
        elements.yearRange.value;
}


function handleWindowResize() {
    if (state.map) {
        state.map.resize();
    }

    if (state.trendChart) {
        state.trendChart.resize();
    }

    if (state.radarChart) {
        state.radarChart.resize();
    }
}


/* ==========================================================
   6. MAP INITIALIZATION
   ========================================================== */

function initializeMap() {
    state.map = new maplibregl.Map({
        container: "map",

        style: {
            version: 8,

            sources: {
                satellite: {
                    type: "raster",
                    tiles: CONFIG.satelliteTiles,
                    tileSize: 256,
                    attribution:
                        CONFIG.satelliteAttribution
                }
            },

            layers: [
                {
                    id: "satellite-imagery",
                    type: "raster",
                    source: "satellite",

                    paint: {
                        "raster-opacity": 0.82,
                        "raster-saturation": -0.28,
                        "raster-contrast": 0.18,
                        "raster-brightness-min": 0.03,
                        "raster-brightness-max": 0.72
                    }
                }
            ]
        },

        center: CONFIG.initialCenter,
        zoom: CONFIG.initialZoom,

        minZoom: 3,
        maxZoom: 14,

        pitch: 0,
        bearing: 0,

        attributionControl: true
    });


    state.map.addControl(
        new maplibregl.NavigationControl({
            showCompass: false,
            visualizePitch: false
        }),
        "top-right"
    );


    state.map.on("load", async () => {
        state.mapLoaded = true;

        try {
            await loadBoundaryLayer();
            showInitialKsaView();
        } catch (error) {
            handleApplicationError(
                "The KSA boundary layer could not be loaded.",
                error
            );
        }
    });


    state.map.on("error", event => {
        if (event && event.error) {
            console.error(
                "MapLibre error:",
                event.error
            );
        }
    });
}


/* ==========================================================
   7. LOAD GEOJSON
   ========================================================== */

async function loadBoundaryLayer() {
    const boundaryUrl =
        `${CONFIG.apiBaseUrl}/api/boundaries`;

    const response = await fetch(boundaryUrl, {
        cache: "no-store"
    });

    if (!response.ok) {
        throw new Error(
            `Boundary request failed with status ` +
            `${response.status}.`
        );
    }

    const geojson = await response.json();

    if (
        !geojson ||
        geojson.type !== "FeatureCollection" ||
        !Array.isArray(geojson.features)
    ) {
        throw new Error(
            "The boundary endpoint did not return a valid " +
            "GeoJSON FeatureCollection."
        );
    }

    state.boundaries = geojson;
    state.riyadhFeature = findRiyadhFeature(geojson);

    state.map.addSource(
        CONFIG.sourceId,
        {
            type: "geojson",
            data: geojson,
            generateId: true
        }
    );

    addInitialBoundaryLayers();
    addSelectedRiyadhLayers();
}


/* ==========================================================
   8. INITIAL KSA BOUNDARY LAYERS
   ========================================================== */

function addInitialBoundaryLayers() {
    state.map.addLayer({
        id: CONFIG.layerIds.initialGlow,
        type: "line",
        source: CONFIG.sourceId,

        paint: {
            "line-color":
                CONFIG.initialBoundaryColor,

            "line-width": 8,

            "line-opacity": 0.22,

            "line-blur": 7
        }
    });


    state.map.addLayer({
        id: CONFIG.layerIds.initialOutline,
        type: "line",
        source: CONFIG.sourceId,

        paint: {
            "line-color":
                CONFIG.initialBoundaryColor,

            "line-width": [
                "interpolate",
                ["linear"],
                ["zoom"],
                3,
                0.7,
                6,
                1.4,
                10,
                2.2
            ],

            "line-opacity": 0.78
        }
    });
}


/* ==========================================================
   9. SELECTED RIYADH LAYERS
   ========================================================== */

function addSelectedRiyadhLayers() {
    const riyadhFilter =
        createRiyadhMapFilter();


    state.map.addLayer({
        id: CONFIG.layerIds.selectedFill,
        type: "fill",
        source: CONFIG.sourceId,
        filter: riyadhFilter,

        paint: {
            "fill-color": "#f17832",
            "fill-opacity": 0
        }
    });


    state.map.addLayer({
        id: CONFIG.layerIds.selectedOuterGlow,
        type: "line",
        source: CONFIG.sourceId,
        filter: riyadhFilter,

        paint: {
            "line-color":
                CONFIG.selectedBoundaryColor,

            "line-width": 18,

            "line-opacity": 0,

            "line-blur": 12
        }
    });


    state.map.addLayer({
        id: CONFIG.layerIds.selectedGlow,
        type: "line",
        source: CONFIG.sourceId,
        filter: riyadhFilter,

        paint: {
            "line-color":
                CONFIG.selectedBoundaryColor,

            "line-width": 8,

            "line-opacity": 0,

            "line-blur": 5
        }
    });


    state.map.addLayer({
        id: CONFIG.layerIds.selectedOutline,
        type: "line",
        source: CONFIG.sourceId,
        filter: riyadhFilter,

        paint: {
            "line-color":
                "#ff9b5c",

            "line-width": 2.5,

            "line-opacity": 0
        }
    });
}


/* ==========================================================
   10. FIND RIYADH FEATURE
   ========================================================== */

function findRiyadhFeature(geojson) {
    const exactCandidates = [
        "riyadh",
        "al riyadh",
        "ar riyadh",
        "ar riyad",
        "riyadh region",
        "riyadh province",
        "منطقة الرياض",
        "الرياض"
    ];

    for (const feature of geojson.features) {
        const propertyValues =
            Object.values(feature.properties || {});

        for (const propertyValue of propertyValues) {
            if (propertyValue === null) {
                continue;
            }

            const normalizedValue =
                normalizeText(propertyValue);

            if (
                exactCandidates.some(candidate =>
                    normalizedValue ===
                    normalizeText(candidate)
                )
            ) {
                return feature;
            }
        }
    }


    for (const feature of geojson.features) {
        const propertyValues =
            Object.values(feature.properties || {});

        for (const propertyValue of propertyValues) {
            if (propertyValue === null) {
                continue;
            }

            const normalizedValue =
                normalizeText(propertyValue);

            if (
                normalizedValue.includes("riyadh") ||
                normalizedValue.includes("رياض")
            ) {
                return feature;
            }
        }
    }


    console.warn(
        "A Riyadh feature could not be identified " +
        "automatically from the GeoJSON properties."
    );

    return null;
}


function normalizeText(value) {
    return String(value)
        .trim()
        .replace(/^['’]+/, "")
        .toLowerCase()
        .replace(/[_-]+/g, " ")
        .replace(/\s+/g, " ");
}


/*
MapLibre filters can only use known property names.

This filter checks several common English and Arabic
administrative-name fields. The JavaScript also computes the
actual Riyadh geometry separately for zooming.
*/
function createRiyadhMapFilter() {
    const possibleFields = [
        "name",
        "Name",
        "NAME",
        "name_en",
        "NAME_EN",
        "region",
        "Region",
        "REGION",
        "region_name",
        "REGION_NAME",
        "admin1Name",
        "admin1Name_en",
        "ADM1_EN",
        "shapeName",
        "ShapeName",
        "shapeName_en",
        "NAME_1",
        "name_ar",
        "NAME_AR",
        "region_ar",
        "REGION_AR",
        "ADM1_AR"
    ];

    const conditions = possibleFields.map(field => [
        "any",

        [
            "==",
            ["downcase", ["to-string", ["get", field]]],
            "riyadh"
        ],

        [
            "==",
            ["downcase", ["to-string", ["get", field]]],
            "al riyadh"
        ],

        [
            "==",
            ["downcase", ["to-string", ["get", field]]],
            "ar riyadh"
        ],

        [
            "==",
            ["downcase", ["to-string", ["get", field]]],
            "ar riyad"
        ],

        [
            "==",
            ["downcase", ["to-string", ["get", field]]],
            "riyadh region"
        ],

        [
            "==",
            ["to-string", ["get", field]],
            "الرياض"
        ],

        [
            "==",
            ["to-string", ["get", field]],
            "منطقة الرياض"
        ]
    ]);

    return [
        "any",
        ...conditions
    ];
}


/* ==========================================================
   11. INITIAL MAP VIEW
   ========================================================== */

function showInitialKsaView() {
    state.map.fitBounds(
        CONFIG.initialBounds,
        {
            padding: {
                top: 105,
                right: 70,
                bottom: 50,
                left: 70
            },

            duration: 0,
            maxZoom: 5.35
        }
    );

    setInitialLayerVisibility();
}


function setInitialLayerVisibility() {
    setLayerPaintSafely(
        CONFIG.layerIds.initialGlow,
        "line-opacity",
        0.22
    );

    setLayerPaintSafely(
        CONFIG.layerIds.initialOutline,
        "line-opacity",
        0.78
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedFill,
        "fill-opacity",
        0
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedOuterGlow,
        "line-opacity",
        0
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedGlow,
        "line-opacity",
        0
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedOutline,
        "line-opacity",
        0
    );
}


/* ==========================================================
   12. RUN ANALYSIS
   ========================================================== */

async function runAnalysis() {
    if (state.analysisRunning) {
        return;
    }

    clearErrorMessage();

    const selection = getCurrentSelection();

    if (!selection.region) {
        showErrorMessage(
            "Select Riyadh Region before running the analysis."
        );

        elements.regionSelect.focus();
        return;
    }

    if (!state.mapLoaded || !state.boundaries) {
        showErrorMessage(
            "The map is still loading. Please wait briefly " +
            "and run the analysis again."
        );

        return;
    }

    state.analysisRunning = true;

    setLoadingState(true);
    resetResultsContent();

    try {
        const encodedRegion =
            encodeURIComponent(selection.region);

        const encodedCode =
            encodeURIComponent(selection.naceCode);

        const requests = [
            fetchJson(
                `${CONFIG.apiBaseUrl}/api/kpis` +
                `?year=${selection.year}` +
                `&region=${encodedRegion}` +
                `&nace_code=${encodedCode}`
            ),

            fetchJson(
                `${CONFIG.apiBaseUrl}/api/trend` +
                `?region=${encodedRegion}` +
                `&nace_code=${encodedCode}`
            ),

            fetchJson(
                `${CONFIG.apiBaseUrl}/api/sectors` +
                `?year=${selection.year}` +
                `&region=${encodedRegion}`
            )
        ];

        const [
            kpiData,
            trendData,
            sectorData
        ] = await Promise.all(requests);

        populateKpiResults(
            kpiData,
            selection
        );

        populateTrendChart(
            trendData,
            selection
        );

        populateSectorDistribution(
            sectorData,
            selection
        );

        await showAnalysisState();

    } catch (error) {
        handleApplicationError(
            "The economic results could not be loaded.",
            error
        );
    } finally {
        state.analysisRunning = false;
        setLoadingState(false);
    }
}


/* ==========================================================
   13. CURRENT SELECTION
   ========================================================== */

function getCurrentSelection() {
    const sectorOption =
        elements.sectorSelect.options[
            elements.sectorSelect.selectedIndex
        ];

    const metricOption =
        elements.metricSelect.options[
            elements.metricSelect.selectedIndex
        ];

    return {
        region: elements.regionSelect.value,
        naceCode: elements.sectorSelect.value,

        sectorName:
            sectorOption
                ? sectorOption.textContent.trim()
                : "",

        metric: elements.metricSelect.value,

        metricName:
            metricOption
                ? metricOption.textContent.trim()
                : "",

        year: Number(elements.yearRange.value)
    };
}


/* ==========================================================
   14. SHOW POST-ANALYSIS STATE
   ========================================================== */

async function showAnalysisState() {
    state.resultsVisible = true;

    elements.application.classList.add(
        "results-visible"
    );

    elements.resultsPanel.setAttribute(
        "aria-hidden",
        "false"
    );

    elements.selectedGeographyHeading.classList.remove(
        "is-hidden"
    );

    window.requestAnimationFrame(() => {
        state.map.resize();

        zoomToRiyadh();
        highlightRiyadh();
    });

    window.setTimeout(() => {
        if (state.trendChart) {
            state.trendChart.resize();
        }

        if (state.radarChart) {
            state.radarChart.resize();
        }
    }, 720);
}


/* ==========================================================
   15. ZOOM TO RIYADH
   ========================================================== */

function zoomToRiyadh() {
    let bounds = null;

    if (state.riyadhFeature) {
        bounds = calculateGeometryBounds(
            state.riyadhFeature.geometry
        );
    }

    if (!bounds) {
        /*
        Fallback Riyadh-region view if a matching feature
        property cannot be found.
        */
        bounds = [
            [42.2, 19.0],
            [48.4, 28.9]
        ];
    }

    state.map.fitBounds(
        bounds,
        {
            padding: {
                top: 115,
                right: 48,
                bottom: 55,
                left: 48
            },

            duration:
                CONFIG.mapAnimationDuration,

            maxZoom: 7.15,

            essential: true
        }
    );
}


/* ==========================================================
   16. HIGHLIGHT RIYADH
   ========================================================== */

function highlightRiyadh() {
    setLayerPaintSafely(
        CONFIG.layerIds.initialGlow,
        "line-opacity",
        0.07
    );

    setLayerPaintSafely(
        CONFIG.layerIds.initialOutline,
        "line-opacity",
        0.27
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedFill,
        "fill-opacity",
        0.12
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedOuterGlow,
        "line-opacity",
        0.16
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedGlow,
        "line-opacity",
        0.56
    );

    setLayerPaintSafely(
        CONFIG.layerIds.selectedOutline,
        "line-opacity",
        1
    );
}


/* ==========================================================
   17. CLOSE RESULTS PANEL
   ========================================================== */

function closeResultsPanel() {
    state.resultsVisible = false;

    elements.application.classList.remove(
        "results-visible"
    );

    elements.resultsPanel.setAttribute(
        "aria-hidden",
        "true"
    );

    elements.selectedGeographyHeading.classList.add(
        "is-hidden"
    );

    window.setTimeout(() => {
        state.map.resize();

        state.map.fitBounds(
            CONFIG.initialBounds,
            {
                padding: {
                    top: 105,
                    right: 70,
                    bottom: 50,
                    left: 70
                },

                duration: 1600,
                maxZoom: 5.35,
                essential: true
            }
        );

        setInitialLayerVisibility();
    }, 120);
}


/* ==========================================================
   18. KPI RESULTS
   ========================================================== */

function populateKpiResults(
    kpiData,
    selection
) {
    const selectedGva =
        toFiniteNumber(kpiData.gva_value);

    const baseGva =
        toFiniteNumber(kpiData.base_year_gva);

    const share =
        toFiniteNumber(
            kpiData.share_of_total_percent
        );

    const growth =
        toFiniteNumber(
            kpiData.growth_from_base_percent
        );

    const baseYear =
        kpiData.base_year ?? "-";

    elements.kpiGva.textContent =
        formatNumber(selectedGva, 2);

    elements.kpiBaseGva.textContent =
        formatNumber(baseGva, 2);

    elements.kpiBaseGvaYear.textContent =
        baseYear !== "-"
            ? `SAR Million / ${baseYear}`
            : "SAR Million";

    elements.kpiSector.textContent =
        kpiData.sector_name ||
        selection.sectorName ||
        "-";

    elements.kpiShare.textContent =
        share !== null
            ? `${formatNumber(share, 2)}%`
            : "-";

    elements.kpiGrowth.textContent =
        growth !== null
            ? `${formatNumber(growth, 2)}%`
            : "-";

    elements.kpiYear.textContent =
        selection.year;

    elements.trendHighlightValue.textContent =
        selectedGva !== null
            ? formatNumber(selectedGva, 2)
            : "-";

    elements.resultsSelectionSummary.textContent =
        [
            "Riyadh Region",
            selection.sectorName,
            selection.metricName,
            selection.year
        ].join("  •  ");
}


/* ==========================================================
   19. ECHARTS INITIALIZATION
   ========================================================== */

function initializeCharts() {
    state.trendChart = echarts.init(
        elements.trendChartElement,
        null,
        {
            renderer: "canvas"
        }
    );

    state.radarChart = echarts.init(
        elements.radarChartElement,
        null,
        {
            renderer: "canvas"
        }
    );

    renderEmptyTrendChart();
    renderEmptyRadarChart();
}


/* ==========================================================
   20. TREND CHART
   ========================================================== */

function populateTrendChart(
    trendData,
    selection
) {
    const trendRecords =
        Array.isArray(trendData.trend)
            ? trendData.trend
            : [];

    const years = trendRecords.map(
        record => Number(record.year)
    );

    const values = trendRecords.map(
        record => toFiniteNumber(
            record.gva_value
        ) ?? 0
    );

    const selectedIndex =
        years.indexOf(selection.year);

    const markLineData =
        selectedIndex >= 0
            ? [
                {
                    xAxis: String(selection.year)
                }
            ]
            : [];

    state.trendChart.setOption(
        {
            animation: true,
            animationDuration: 900,
            animationEasing: "cubicOut",

            backgroundColor: "transparent",

            tooltip: {
                trigger: "axis",

                backgroundColor:
                    "rgba(8, 14, 27, 0.96)",

                borderColor:
                    "rgba(144, 98, 255, 0.55)",

                textStyle: {
                    color: "#f4f6fb",
                    fontFamily: "Montserrat",
                    fontSize: 10
                },

                formatter(parameters) {
                    const item = parameters[0];

                    return (
                        `<strong>${item.axisValue}</strong>` +
                        `<br>` +
                        `${selection.sectorName}: ` +
                        `${formatNumber(item.value, 2)} ` +
                        `SAR Mn`
                    );
                }
            },

            grid: {
                left: 48,
                right: 18,
                top: 25,
                bottom: 38
            },

            xAxis: {
                type: "category",
                boundaryGap: false,
                data: years.map(String),

                axisLine: {
                    lineStyle: {
                        color:
                            "rgba(178, 192, 220, 0.18)"
                    }
                },

                axisTick: {
                    show: false
                },

                axisLabel: {
                    color: "#778092",
                    fontFamily: "Montserrat",
                    fontSize: 8,
                    interval: calculateAxisInterval(
                        years.length
                    )
                }
            },

            yAxis: {
                type: "value",

                axisLine: {
                    show: false
                },

                axisTick: {
                    show: false
                },

                splitLine: {
                    lineStyle: {
                        color:
                            "rgba(174, 189, 218, 0.08)"
                    }
                },

                axisLabel: {
                    color: "#778092",
                    fontFamily: "Montserrat",
                    fontSize: 8,

                    formatter(value) {
                        return abbreviateNumber(value);
                    }
                }
            },

            series: [
                {
                    name:
                        `${selection.sectorName} GVA`,

                    type: "line",

                    data: values,

                    smooth: 0.28,
                    symbol: "circle",
                    symbolSize: 5,

                    showSymbol: true,
                    connectNulls: false,

                    lineStyle: {
                        width: 2.2,
                        color: "#3bd477",

                        shadowColor:
                            "rgba(59, 212, 119, 0.45)",

                        shadowBlur: 10
                    },

                    itemStyle: {
                        color: "#3bd477",
                        borderColor: "#b9ffd5",
                        borderWidth: 1
                    },

                    areaStyle: {
                        color: {
                            type: "linear",
                            x: 0,
                            y: 0,
                            x2: 0,
                            y2: 1,

                            colorStops: [
                                {
                                    offset: 0,
                                    color:
                                        "rgba(59, 212, 119, 0.24)"
                                },
                                {
                                    offset: 1,
                                    color:
                                        "rgba(59, 212, 119, 0)"
                                }
                            ]
                        }
                    },

                    markLine: {
                        silent: true,
                        symbol: "none",

                        label: {
                            show: false
                        },

                        lineStyle: {
                            color:
                                "rgba(255, 145, 80, 0.52)",

                            type: "dashed",
                            width: 1
                        },

                        data: markLineData
                    }
                }
            ]
        },
        true
    );
}


function renderEmptyTrendChart() {
    state.trendChart.setOption({
        backgroundColor: "transparent",

        graphic: [
            {
                type: "text",
                left: "center",
                top: "middle",

                style: {
                    text:
                        "Run the analysis to display the GVA trend",
                    fill: "#6f788a",
                    font:
                        "11px Montserrat",
                    textAlign: "center"
                }
            }
        ]
    });
}


/* ==========================================================
   21. RADAR CHART AND SECTOR TABLE
   ========================================================== */

function populateSectorDistribution(
    sectorData,
    selection
) {
    const sectorRecords =
        Array.isArray(sectorData.sectors)
            ? sectorData.sectors
            : [];

    const maximumValue = Math.max(
        ...sectorRecords.map(
            record =>
                toFiniteNumber(
                    record.gva_value
                ) ?? 0
        ),
        1
    );

    const radarMaximum =
        Math.ceil(maximumValue * 1.12);

    const indicators = sectorRecords.map(
        record => ({
            name: shortenSectorName(
                record.sector_name
            ),

            max: radarMaximum
        })
    );

    const values = sectorRecords.map(
        record =>
            toFiniteNumber(
                record.gva_value
            ) ?? 0
    );

    state.radarChart.setOption(
        {
            animation: true,
            animationDuration: 900,
            animationEasing: "cubicOut",

            backgroundColor: "transparent",

            tooltip: {
                trigger: "item",

                backgroundColor:
                    "rgba(8, 14, 27, 0.96)",

                borderColor:
                    "rgba(144, 98, 255, 0.55)",

                textStyle: {
                    color: "#f4f6fb",
                    fontFamily: "Montserrat",
                    fontSize: 9
                }
            },

            radar: {
                center: ["50%", "52%"],
                radius: "62%",
                startAngle: 90,

                splitNumber: 5,

                indicator: indicators,

                axisName: {
                    color: "#d7dce6",
                    fontFamily: "Montserrat",
                    fontSize: 8,
                    lineHeight: 11
                },

                axisLine: {
                    lineStyle: {
                        color:
                            "rgba(187, 200, 225, 0.18)"
                    }
                },

                splitLine: {
                    lineStyle: {
                        color:
                            "rgba(187, 200, 225, 0.13)"
                    }
                },

                splitArea: {
                    show: true,

                    areaStyle: {
                        color: [
                            "rgba(143, 81, 255, 0.016)",
                            "rgba(143, 81, 255, 0.032)"
                        ]
                    }
                }
            },

            series: [
                {
                    name:
                        `Riyadh GVA ${selection.year}`,

                    type: "radar",

                    data: [
                        {
                            value: values,
                            name:
                                `Sector distribution ${selection.year}`,

                            symbol: "circle",
                            symbolSize: 4,

                            lineStyle: {
                                width: 2,
                                color: "#a66cff"
                            },

                            itemStyle: {
                                color: "#ff8852"
                            },

                            areaStyle: {
                                color:
                                    "rgba(143, 81, 255, 0.32)"
                            }
                        }
                    ]
                }
            ]
        },
        true
    );

    elements.sectorDistributionSubtitle.textContent =
        `SAR Million / ${selection.year}`;

    populateSectorTable(
        sectorRecords,
        selection.naceCode
    );
}


function populateSectorTable(
    sectorRecords,
    selectedNaceCode
) {
    elements.sectorResultsTableBody.innerHTML = "";

    sectorRecords.forEach(record => {
        const tableRow =
            document.createElement("tr");

        const sectorName =
            document.createElement("td");

        const gvaValue =
            document.createElement("td");

        const shareValue =
            document.createElement("td");

        sectorName.textContent =
            record.sector_name || "-";

        gvaValue.textContent =
            formatNumber(
                toFiniteNumber(
                    record.gva_value
                ),
                2
            );

        const share =
            toFiniteNumber(
                record.share_percent
            );

        shareValue.textContent =
            share !== null
                ? `${formatNumber(share, 2)}%`
                : "-";

        if (
            sectorMatchesSelection(
                record.sector_name,
                selectedNaceCode
            )
        ) {
            tableRow.classList.add(
                "selected-sector-row"
            );
        }

        tableRow.appendChild(sectorName);
        tableRow.appendChild(gvaValue);
        tableRow.appendChild(shareValue);

        elements.sectorResultsTableBody.appendChild(
            tableRow
        );
    });
}


function renderEmptyRadarChart() {
    state.radarChart.setOption({
        backgroundColor: "transparent",

        graphic: [
            {
                type: "text",
                left: "center",
                top: "middle",

                style: {
                    text:
                        "Run the analysis to display sector distribution",
                    fill: "#6f788a",
                    font:
                        "11px Montserrat",
                    textAlign: "center"
                }
            }
        ]
    });
}


/* ==========================================================
   22. RESET RESULTS
   ========================================================== */

function resetResultsContent() {
    elements.kpiGva.textContent = "-";
    elements.kpiBaseGva.textContent = "-";
    elements.kpiBaseGvaYear.textContent =
        "SAR Million";
    elements.kpiSector.textContent = "-";
    elements.kpiShare.textContent = "-";
    elements.kpiGrowth.textContent = "-";
    elements.kpiYear.textContent =
        elements.yearRange.value;

    elements.trendHighlightValue.textContent = "-";

    elements.sectorResultsTableBody.innerHTML = "";
}


/* ==========================================================
   23. API HELPER
   ========================================================== */

async function fetchJson(url) {
    const response = await fetch(
        url,
        {
            method: "GET",

            headers: {
                Accept: "application/json"
            },

            cache: "no-store"
        }
    );

    if (!response.ok) {
        let message =
            `Request failed with status ${response.status}.`;

        try {
            const errorBody =
                await response.json();

            if (errorBody && errorBody.detail) {
                message = errorBody.detail;
            }
        } catch {
            /*
            Keep the original HTTP status message.
            */
        }

        throw new Error(message);
    }

    return response.json();
}


/* ==========================================================
   24. GEOJSON BOUNDS
   ========================================================== */

function calculateGeometryBounds(geometry) {
    if (!geometry || !geometry.coordinates) {
        return null;
    }

    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;

    walkCoordinates(
        geometry.coordinates,
        coordinate => {
            if (
                !Array.isArray(coordinate) ||
                coordinate.length < 2
            ) {
                return;
            }

            const longitude =
                Number(coordinate[0]);

            const latitude =
                Number(coordinate[1]);

            if (
                !Number.isFinite(longitude) ||
                !Number.isFinite(latitude)
            ) {
                return;
            }

            west = Math.min(west, longitude);
            south = Math.min(south, latitude);
            east = Math.max(east, longitude);
            north = Math.max(north, latitude);
        }
    );

    if (
        !Number.isFinite(west) ||
        !Number.isFinite(south) ||
        !Number.isFinite(east) ||
        !Number.isFinite(north)
    ) {
        return null;
    }

    return [
        [west, south],
        [east, north]
    ];
}


function walkCoordinates(
    coordinates,
    callback
) {
    if (
        Array.isArray(coordinates) &&
        coordinates.length >= 2 &&
        typeof coordinates[0] === "number" &&
        typeof coordinates[1] === "number"
    ) {
        callback(coordinates);
        return;
    }

    if (Array.isArray(coordinates)) {
        coordinates.forEach(child =>
            walkCoordinates(
                child,
                callback
            )
        );
    }
}


/* ==========================================================
   25. LAYER HELPER
   ========================================================== */

function setLayerPaintSafely(
    layerId,
    property,
    value
) {
    if (
        state.map &&
        state.map.getLayer(layerId)
    ) {
        state.map.setPaintProperty(
            layerId,
            property,
            value
        );
    }
}


/* ==========================================================
   26. LOADING STATE
   ========================================================== */

function setLoadingState(isLoading) {
    elements.mapLoadingOverlay.classList.toggle(
        "is-hidden",
        !isLoading
    );

    elements.runButton.disabled = isLoading;

    const buttonText =
        elements.runButton.querySelector(
            "span:first-child"
        );

    if (buttonText) {
        buttonText.textContent =
            isLoading
                ? "Running Analysis..."
                : "Run Analysis";
    }
}


/* ==========================================================
   27. ERROR HANDLING
   ========================================================== */

function handleApplicationError(
    userMessage,
    error
) {
    console.error(userMessage, error);

    const technicalMessage =
        error instanceof Error
            ? error.message
            : String(error);

    showErrorMessage(
        `${userMessage} ${technicalMessage}`
    );
}


function showErrorMessage(message) {
    elements.mapErrorMessage.textContent =
        message;

    elements.mapErrorMessage.classList.remove(
        "is-hidden"
    );

    window.clearTimeout(
        showErrorMessage.timeoutId
    );

    showErrorMessage.timeoutId =
        window.setTimeout(
            clearErrorMessage,
            8000
        );
}


function clearErrorMessage() {
    elements.mapErrorMessage.textContent = "";

    elements.mapErrorMessage.classList.add(
        "is-hidden"
    );
}


/* ==========================================================
   28. FORMATTING UTILITIES
   ========================================================== */

function toFiniteNumber(value) {
    const numberValue = Number(value);

    return Number.isFinite(numberValue)
        ? numberValue
        : null;
}


function formatNumber(
    value,
    decimalPlaces = 2
) {
    const numberValue =
        toFiniteNumber(value);

    if (numberValue === null) {
        return "-";
    }

    return new Intl.NumberFormat(
        "en-US",
        {
            minimumFractionDigits:
                decimalPlaces,

            maximumFractionDigits:
                decimalPlaces
        }
    ).format(numberValue);
}


function abbreviateNumber(value) {
    const numberValue =
        toFiniteNumber(value);

    if (numberValue === null) {
        return "-";
    }

    const absoluteValue =
        Math.abs(numberValue);

    if (absoluteValue >= 1_000_000) {
        return (
            `${formatCompactDecimal(
                numberValue / 1_000_000
            )}M`
        );
    }

    if (absoluteValue >= 1_000) {
        return (
            `${formatCompactDecimal(
                numberValue / 1_000
            )}K`
        );
    }

    return formatCompactDecimal(
        numberValue
    );
}


function formatCompactDecimal(value) {
    if (Math.abs(value) >= 100) {
        return value.toFixed(0);
    }

    if (Math.abs(value) >= 10) {
        return value.toFixed(1);
    }

    return value.toFixed(2);
}


function calculateAxisInterval(itemCount) {
    if (itemCount <= 8) {
        return 0;
    }

    if (itemCount <= 16) {
        return 1;
    }

    return 2;
}


function shortenSectorName(name) {
    const replacements = {
        "Transport, storage, information & communication services":
            "Transport & ICT",

        "Financial & business services":
            "Financial & Business",

        "Consumer services":
            "Consumer Services",

        "Public services":
            "Public Services",

        "Agriculture":
            "Agriculture",

        "Industry":
            "Industry"
    };

    return (
        replacements[name] ||
        truncateText(name, 19)
    );
}


function truncateText(
    text,
    maximumLength
) {
    const stringValue =
        String(text || "");

    if (
        stringValue.length <= maximumLength
    ) {
        return stringValue;
    }

    return (
        stringValue.slice(
            0,
            maximumLength - 1
        ) + "…"
    );
}


function sectorMatchesSelection(
    sectorName,
    naceCode
) {
    const mapping = {
        "A": "Agriculture",
        "B-E": "Industry",
        "F": "Industry",
        "G-I": "Consumer services",
        "J":
            "Transport, storage, information & communication services",
        "K": "Financial & business services",
        "L": "Financial & business services",
        "M_N": "Financial & business services",
        "O-Q": "Public services",
        "R-U": "Consumer services"
    };

    return (
        normalizeText(sectorName) ===
        normalizeText(mapping[naceCode] || "")
    );
}


/* ==========================================================
   29. GENERAL UTILITY
   ========================================================== */

function debounce(
    callback,
    waitMilliseconds
) {
    let timeoutId;

    return function debouncedFunction(...args) {
        window.clearTimeout(timeoutId);

        timeoutId = window.setTimeout(
            () => callback.apply(
                this,
                args
            ),
            waitMilliseconds
        );
    };
}
