#!/usr/bin/env python3
"""Writes topo.json, satellite.json and usgs-topo.json next to style.json.

    python3 make_styles.py https://sashalawrence13.github.io/shenandoah-outdoors-basemap .

The base URL is an argument so the same styles can be served from a
local folder while testing (e.g. http://localhost:8765).
"""
import json, sys
from pathlib import Path

BASE = sys.argv[1].rstrip("/")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else ".")

OMT_ATTR = "© OpenMapTiles © OpenStreetMap contributors"
USGS = "https://basemap.nationalmap.gov/arcgis/rest/services"

def omt():
    return {"type": "vector", "tiles": [f"{BASE}/tiles/{{z}}/{{x}}/{{y}}.pbf"],
            "minzoom": 0, "maxzoom": 14, "attribution": OMT_ATTR}

NAME = ["coalesce", ["get", "name_en"], ["get", "name"]]
REG, ITAL, BOLD = ["Noto Sans Regular"], ["Noto Sans Italic"], ["Noto Sans Bold"]
lin = lambda *stops: ["interpolate", ["linear"], ["zoom"], *stops]
cls = lambda *c: ["in", ["get", "class"], ["literal", list(c)]]

def labels(dark):
    """Place, peak, water and road names. `dark` = on imagery."""
    text, halo = ("#ffffff", "rgba(0,0,0,0.75)") if dark else ("#3d3a33", "rgba(255,255,255,0.9)")
    water = "#d6ecff" if dark else "#3f78a8"
    peak = "#ffffff" if dark else "#6b4a26"
    return [
        {"id": "label-waterway", "type": "symbol", "source": "openmaptiles", "source-layer": "waterway",
         "minzoom": 12, "filter": ["has", "name"],
         "layout": {"symbol-placement": "line", "text-field": NAME, "text-font": ITAL, "text-size": 11,
                    "symbol-spacing": 400, "text-max-angle": 30},
         "paint": {"text-color": water, "text-halo-color": halo, "text-halo-width": 1.3}},
        {"id": "label-water", "type": "symbol", "source": "openmaptiles", "source-layer": "water_name",
         "minzoom": 11, "layout": {"text-field": NAME, "text-font": ITAL, "text-size": 11},
         "paint": {"text-color": water, "text-halo-color": halo, "text-halo-width": 1.3}},
        {"id": "label-road", "type": "symbol", "source": "openmaptiles", "source-layer": "transportation_name",
         "minzoom": 13, "filter": ["!", cls("path", "track")],
         "layout": {"symbol-placement": "line", "text-field": NAME, "text-font": REG, "text-size": 10,
                    "symbol-spacing": 350},
         "paint": {"text-color": text, "text-halo-color": halo, "text-halo-width": 1.3}},
        {"id": "label-peak", "type": "symbol", "source": "openmaptiles", "source-layer": "mountain_peak",
         "minzoom": 11, "filter": ["==", ["get", "class"], "peak"],
         "layout": {"text-field": ["case", ["has", "ele_ft"],
                                   ["concat", NAME, "\n", ["to-string", ["get", "ele_ft"]], " ft"], NAME],
                    "text-font": BOLD, "text-size": 11, "text-line-height": 1.1, "symbol-sort-key": ["get", "rank"]},
         "paint": {"text-color": peak, "text-halo-color": halo, "text-halo-width": 1.5}},
        {"id": "label-place", "type": "symbol", "source": "openmaptiles", "source-layer": "place",
         "minzoom": 8, "filter": cls("city", "town", "village", "hamlet"),
         "layout": {"text-field": NAME, "text-font": BOLD,
                    "text-size": ["match", ["get", "class"], ["city", "town"], 13, "village", 12, 11],
                    "symbol-sort-key": ["get", "rank"]},
         "paint": {"text-color": text, "text-halo-color": halo, "text-halo-width": 1.6}},
    ]

def roads(colors, casing=None):
    path, minor, sec, pri, mot = colors
    return [
        {"id": "road-path", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
         "filter": cls("path", "track"), "minzoom": 12,
         "paint": {"line-color": path, "line-width": 0.9, "line-dasharray": [2, 1.5]}},
        {"id": "road-minor", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
         "filter": cls("minor", "service"), "minzoom": 11,
         "paint": {"line-color": minor, "line-width": lin(11, 0.6, 16, 2.4)}},
        {"id": "road-secondary", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
         "filter": cls("tertiary", "secondary"), "minzoom": 8,
         "paint": {"line-color": sec, "line-width": lin(8, 0.7, 16, 3.4)}},
        {"id": "road-primary", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
         "filter": cls("primary", "trunk"), "minzoom": 6,
         "paint": {"line-color": pri, "line-width": lin(6, 0.9, 16, 4.5)}},
        {"id": "road-motorway", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
         "filter": ["==", ["get", "class"], "motorway"], "minzoom": 4,
         "paint": {"line-color": mot, "line-width": lin(4, 0.9, 16, 5.5)}},
    ]

PAPER = "#f4f1e8"
topo = {
    "version": 8, "name": "mossback-topo",
    "glyphs": f"{BASE}/glyphs/{{fontstack}}/{{range}}.pbf",
    "sources": {
        "openmaptiles": omt(),
        "contours": {"type": "vector", "tiles": [f"{BASE}/contours/{{z}}/{{x}}/{{y}}.pbf"],
                     "minzoom": 10, "maxzoom": 14, "attribution": "Contours: USGS 3DEP"},
        "dem": {"type": "raster-dem", "tiles": [f"{BASE}/dem/{{z}}/{{x}}/{{y}}.png"], "encoding": "terrarium",
                "tileSize": 256, "minzoom": 8, "maxzoom": 12, "attribution": "Elevation: USGS 3DEP"},
    },
    "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": PAPER}},
        {"id": "landcover-wood", "type": "fill", "source": "openmaptiles", "source-layer": "landcover",
         "filter": ["==", ["get", "class"], "wood"], "paint": {"fill-color": "#cfe0bf", "fill-opacity": 0.85}},
        {"id": "landcover-grass", "type": "fill", "source": "openmaptiles", "source-layer": "landcover",
         "filter": cls("grass", "wetland", "farmland"), "paint": {"fill-color": "#e7ebd3", "fill-opacity": 0.7}},
        {"id": "landuse-residential", "type": "fill", "source": "openmaptiles", "source-layer": "landuse",
         "filter": ["==", ["get", "class"], "residential"], "paint": {"fill-color": "#ebe6dc", "fill-opacity": 0.6}},
        {"id": "hillshade", "type": "hillshade", "source": "dem",
         "paint": {"hillshade-exaggeration": lin(8, 0.55, 12, 0.45, 15, 0.35),
                   "hillshade-shadow-color": "#3b3122", "hillshade-highlight-color": "rgba(255,255,245,0.35)",
                   "hillshade-accent-color": "#4d4230", "hillshade-illumination-anchor": "map"}},
        {"id": "water", "type": "fill", "source": "openmaptiles", "source-layer": "water",
         "paint": {"fill-color": "#9cc4e0"}},
        {"id": "waterway", "type": "line", "source": "openmaptiles", "source-layer": "waterway",
         "paint": {"line-color": "#6fa6d2", "line-width": lin(8, 0.5, 12, 1, 16, 2.2)}},
        {"id": "contour-minor", "type": "line", "source": "contours", "source-layer": "contour",
         "minzoom": 12.5, "filter": ["==", ["get", "idx"], 0],
         "paint": {"line-color": "#a88660", "line-width": lin(12, 0.4, 16, 0.8),
                   "line-opacity": lin(12, 0.25, 13, 0.5, 15, 0.6)}},
        {"id": "contour-index", "type": "line", "source": "contours", "source-layer": "contour",
         "filter": ["==", ["get", "idx"], 1],
         "paint": {"line-color": "#8f6a42", "line-width": lin(10, 0.6, 16, 1.4),
                   "line-opacity": lin(10, 0.45, 13, 0.75)}},
        {"id": "building", "type": "fill", "source": "openmaptiles", "source-layer": "building", "minzoom": 13,
         "paint": {"fill-color": "#d9d2c3", "fill-opacity": 0.8}},
        {"id": "boundary-admin", "type": "line", "source": "openmaptiles", "source-layer": "boundary",
         "filter": ["<=", ["get", "admin_level"], 4],
         "paint": {"line-color": "#a39e90", "line-width": 0.8, "line-dasharray": [3, 2]}},
        *roads(("#9a7447", "#ffffff", "#f7e7b4", "#f2cf7c", "#e6a95a")),
        {"id": "label-contour", "type": "symbol", "source": "contours", "source-layer": "contour",
         "minzoom": 12.5, "filter": ["==", ["get", "idx"], 1],
         "layout": {"symbol-placement": "line", "text-field": ["to-string", ["get", "ele_ft"]],
                    "text-font": REG, "text-size": 9.5, "symbol-spacing": 320, "text-max-angle": 25,
                    "text-padding": 2},
         "paint": {"text-color": "#7e5b35", "text-halo-color": PAPER, "text-halo-width": 1.6}},
        *labels(dark=False),
    ],
}

satellite = {
    "version": 8, "name": "mossback-satellite",
    "glyphs": f"{BASE}/glyphs/{{fontstack}}/{{range}}.pbf",
    "sources": {
        "imagery": {"type": "raster", "tiles": [f"{USGS}/USGSImageryOnly/MapServer/tile/{{z}}/{{y}}/{{x}}"],
                    "tileSize": 256, "minzoom": 0, "maxzoom": 16,
                    "attribution": "Imagery: USGS The National Map (USDA NAIP)"},
        "openmaptiles": omt(),
    },
    "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#2b2f26"}},
        {"id": "imagery", "type": "raster", "source": "imagery",
         "paint": {"raster-fade-duration": 150}},
        {"id": "road-major", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
         "minzoom": 9, "filter": cls("motorway", "trunk", "primary", "secondary", "tertiary"),
         "paint": {"line-color": "rgba(255,244,214,0.55)", "line-width": lin(9, 0.6, 16, 2.5)}},
        *labels(dark=True),
    ],
}

usgs_topo = {
    "version": 8, "name": "mossback-usgs-topo",
    "sources": {
        "usgs-topo": {"type": "raster", "tiles": [f"{USGS}/USGSTopo/MapServer/tile/{{z}}/{{y}}/{{x}}"],
                      "tileSize": 256, "minzoom": 0, "maxzoom": 16,
                      "attribution": "USGS The National Map: USGS Topo"},
    },
    "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#f2f0e6"}},
        {"id": "usgs-topo", "type": "raster", "source": "usgs-topo", "paint": {"raster-fade-duration": 150}},
    ],
}

for name, style in (("topo.json", topo), ("satellite.json", satellite), ("usgs-topo.json", usgs_topo)):
    (OUT / name).write_text(json.dumps(style, ensure_ascii=False, separators=(",", ":")))
    print("wrote", OUT / name)
