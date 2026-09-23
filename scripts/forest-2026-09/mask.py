"""Coverage mask for contour and elevation tiles: Shenandoah NP plus the
three GW National Forest districts (phase 1), buffered ~3 km, so the tiles
don't fill the whole valley between them. Writes mask.npz (lon/lat grid)."""
import json, numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation
APP = "/Users/sashalawrence/Documents/Hiking:Backpacking app/src/data/official"
W, S, E, N = -80.1, 37.3, -77.75, 39.2
RES = 0.002  # degrees (~200 m)
nx, ny = int((E - W) / RES) + 1, int((N - S) / RES) + 1
img = Image.new("L", (nx, ny), 0); d = ImageDraw.Draw(img)
def polys(g):
    return [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
feats = json.load(open(f"{APP}/gwnf/areas.json"))["features"] + json.load(open(f"{APP}/shenandoah/boundary.json"))["features"]
for f in feats:
    if f["properties"].get("kind") not in (None, "district"): continue
    for p in polys(f["geometry"]):
        ring = [((x - W) / RES, (N - y) / RES) for x, y in p[0]]
        d.polygon(ring, fill=1)
m = np.array(img, dtype=bool)
r = int(0.03 / RES)
yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
m = binary_dilation(m, structure=(xx * xx + yy * yy) <= r * r)
np.savez_compressed("/private/tmp/gwnf-basemap/mask.npz", m=m, W=W, N=N, RES=RES)
print("mask", m.shape, round(m.mean(), 3))
