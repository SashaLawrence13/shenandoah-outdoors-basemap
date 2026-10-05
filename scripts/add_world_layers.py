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


def apply(path):
    style = json.load(open(path))
    layers = [l for l in style["layers"]
              if not l["id"].startswith("world-") and l["id"] != "ours-cover"]
    style["sources"] = {k: v for k, v in style["sources"].items()
                        if k not in ("world", "ours-cover")}
    style["sources"]["world"] = WORLD
    # Ours never asks for tiles it doesn't have.
    style["sources"][OURS]["bounds"] = [W, S, E, N]
    bg = next((l for l in layers if l["type"] == "background"), None)
    has_raster_base = any(l["type"] == "raster" for l in layers)
    world = []
    for l in layers:
        if l.get("source") != OURS:
            continue
        w = copy.deepcopy(l)
        w["id"] = f"world-{l['id']}"
        w["source"] = "world"
        world.append(w)
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
