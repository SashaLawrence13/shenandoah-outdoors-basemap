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
import copy, json, os, sys

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
# Contours reach past the box: every region in contour_regions.json
# (scripts/make_region_contours.py). One bbox over all of them; tiles
# that don't exist inside it just 404.
REGIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contour_regions.json")
CONTOUR_BOUNDS = [W, S, E, N]
# Regions whose tiles live on another Pages project (mossback-maps is near
# the 20,000-file cap): "host": "maps-2" in contour_regions.json means
# https://mossback-maps-2.pages.dev/contours/. Each gets its own tight
# source (contours-<name>) and a copy of the contour layers, so the main
# source's bounds stay where its tiles are.
EXTRA = []
for r in json.load(open(REGIONS)) if os.path.exists(REGIONS) else []:
    b = r["dem_bbox"]
    if r.get("host", "maps") != "maps":
        # one source per host: bounds = the box over its regions' DEM boxes
        # (tiles outside the regions 404 harmlessly, as in the main source)
        x = next((e for e in EXTRA if e["name"] == r["host"]), None)
        if x is None:
            EXTRA.append({"name": r["host"], "bounds": list(b),
                          "url": f"https://mossback-{r['host']}.pages.dev/contours/{{z}}/{{x}}/{{y}}.pbf"})
        else:
            x["bounds"] = [min(x["bounds"][0], b[0]), min(x["bounds"][1], b[1]),
                           max(x["bounds"][2], b[2]), max(x["bounds"][3], b[3])]
        continue
    CONTOUR_BOUNDS = [min(CONTOUR_BOUNDS[0], b[0]), min(CONTOUR_BOUNDS[1], b[1]),
                      max(CONTOUR_BOUNDS[2], b[2]), max(CONTOUR_BOUNDS[3], b[3])]


def apply(path):
    style = json.load(open(path))
    layers = [l for l in style["layers"]
              if not l["id"].startswith("world-") and l["id"] != "ours-cover"
              and not str(l.get("source", "")).startswith("contours-")]
    style["sources"] = {k: v for k, v in style["sources"].items()
                        if k not in ("world", "world-dem", "ours-cover")
                        and not k.startswith("contours-")}
    style["sources"]["world"] = WORLD
    if "dem" in style["sources"]:
        style["sources"]["world-dem"] = WORLD_DEM
    # Ours never asks for tiles it doesn't have.
    style["sources"][OURS]["bounds"] = [W, S, E, N]
    # Ours draw above the cover, so they must stop at the box too, or the
    # padded elevation tiles would shade twice in a band around it.
    # Contours have no world copy, so they can reach further (no double
    # drawing); they sit above the cover and draw over the world map.
    if "dem" in style["sources"]:
        style["sources"]["dem"]["bounds"] = [W, S, E, N]
    if "contours" in style["sources"]:
        style["sources"]["contours"]["bounds"] = CONTOUR_BOUNDS
        for x in EXTRA:
            style["sources"]["contours-" + x["name"]] = {
                **{k: v for k, v in style["sources"]["contours"].items() if k != "tiles"},
                "tiles": [x["url"]], "bounds": x["bounds"]}
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
    for l in rest:
        out.append(l)
        if l.get("source") == "contours":
            for x in EXTRA:
                c = copy.deepcopy(l)
                c["id"] = f"{l['id']}-{x['name']}"
                c["source"] = "contours-" + x["name"]
                out.append(c)
    style["layers"] = out
    json.dump(style, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print(path, "world layers", len(world),
          "cover" if "ours-cover" in style["sources"] else "no cover")


for p in sys.argv[1:]:
    apply(p)
