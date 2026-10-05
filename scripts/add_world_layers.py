"""Fill the map in outside our own tiles with OpenFreeMap's planet tiles
(free, no key, same OpenMapTiles schema, attribution required).

Our tiles cover one box (the park and the GW districts). Outside it the
map was blank background. Each style gets, in order:
  background, world-* copies of our openmaptiles layers (source "world"),
  a cover cut to our box in the background colour, then our layers.
The cover hides the world copies where ours exist, so see-through layers
(forest shading, paths) don't double up and the park looks unchanged.
Satellite has US-wide imagery under everything, so it gets the world
copies (roads, labels) without a cover. Idempotent: rerunning replaces.

  python3 scripts/add_world_layers.py outdoors.json topo.json satellite.json
"""
import copy, json, sys

WORLD = {
    "type": "vector",
    "url": "https://tiles.openfreemap.org/planet",
    "attribution": "OpenFreeMap © OpenMapTiles Data from OpenStreetMap",
}
# Our tiles' complete area (gwnf-basemap/tiles.sh BOX; the older park
# extract, -79.05,37.9,-77.75,39.15, sits inside it).
W, S, E, N = -80.1, 37.3, -77.75, 39.2
OURS = "openmaptiles"
# Terrain shading everywhere: AWS Open Data terrain tiles (Tilezen,
# terrarium encoding; free, no key; US data is USGS 3DEP).
WORLD_DEM = {
    "type": "raster-dem",
    "tiles": ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
    "encoding": "terrarium",
    "tileSize": 256,
    "maxzoom": 13,
    "attribution": "Terrain: Tilezen (USGS 3DEP, SRTM, GMTED)",
}
COPIED = {OURS: "world", "dem": "world-dem"}


def apply(path):
    style = json.load(open(path))
    layers = [l for l in style["layers"]
              if not l["id"].startswith("world-") and l["id"] != "ours-cover"]
    style["sources"] = {k: v for k, v in style["sources"].items()
                        if k not in ("world", "world-dem", "ours-cover")}
    style["sources"]["world"] = WORLD
    if "dem" in style["sources"]:
        style["sources"]["world-dem"] = WORLD_DEM
    # Ours never asks for tiles it doesn't have.
    style["sources"][OURS]["bounds"] = [W, S, E, N]
    # Ours draw above the cover, so they must stop at the box too, or the
    # padded elevation tiles would shade twice in a band around it.
    for k in ("dem", "contours"):
        if k in style["sources"]:
            style["sources"][k]["bounds"] = [W, S, E, N]
    bg = next((l for l in layers if l["type"] == "background"), None)
    has_raster_base = any(l["type"] == "raster" for l in layers)
    world = []
    for l in layers:
        if l.get("source") not in COPIED:
            continue
        w = copy.deepcopy(l)
        w["id"] = f"world-{l['id']}"
        w["source"] = COPIED[l["source"]]
        world.append(w)
    # Outside our box nothing draws trails on top (in the park the app's
    # own trail lines do), so the world's hiking paths need to read as
    # trails: a firm brown dash, and their names. Footways in towns and
    # dirt tracks stay faint.
    # OpenMapTiles has no sidewalk flag, and named hiking trails often come
    # as subclass "footway" (Cranberry's Middle Fork Trail), so every path
    # counts; only dirt tracks stay faint.
    trailish = ["==", ["get", "class"], "path"]
    for w in list(world):
        if w["id"] != "world-road-path":
            continue
        faint = copy.deepcopy(w)
        faint["id"] = "world-road-path-other"
        faint["filter"] = ["all", w["filter"], ["!", trailish]]
        w["id"] = "world-trail"
        w["filter"] = trailish
        w["minzoom"] = 11
        w["paint"] = {
            "line-color": "#9a4a1f",
            "line-width": ["interpolate", ["linear"], ["zoom"],
                           11, 1, 13, 1.6, 15, 2.4, 17, 3.2],
            "line-dasharray": [3, 1.6],
            "line-opacity": 0.95,
        }
        world.insert(world.index(w), faint)
        label = {
            "id": "world-label-trail", "type": "symbol",
            "source": "world", "source-layer": "transportation_name",
            "minzoom": 13,
            "filter": ["==", ["get", "class"], "path"],
            "layout": {"symbol-placement": "line",
                       "text-field": ["coalesce", ["get", "name_en"], ["get", "name"]],
                       "text-font": ["Noto Sans Regular"], "text-size": 10.5,
                       "symbol-spacing": 300},
            "paint": {"text-color": "#7a3a15",
                      "text-halo-color": "rgba(255,255,255,0.9)",
                      "text-halo-width": 1.4},
        }
        world.append(label)
    out = []
    head = [l for l in layers if l["type"] in ("background", "raster")]
    rest = [l for l in layers if l not in head]
    out += head + world
    if not has_raster_base and bg is not None:
        style["sources"]["ours-cover"] = {
            "type": "geojson",
            "data": {"type": "Feature", "properties": {}, "geometry": {
                "type": "Polygon",
                "coordinates": [[[W, S], [E, S], [E, N], [W, N], [W, S]]]}},
        }
        out.append({
            "id": "ours-cover", "type": "fill", "source": "ours-cover",
            "paint": {"fill-color": bg["paint"]["background-color"],
                      "fill-antialias": False},
        })
    out += rest
    style["layers"] = out
    json.dump(style, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print(path, "world layers", len(world),
          "cover" if "ours-cover" in style["sources"] else "no cover")


for p in sys.argv[1:]:
    apply(p)
