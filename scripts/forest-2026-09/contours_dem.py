"""Contours (40 ft, index every 200 ft) and terrarium DEM tiles for the forest
extension, same method as the basemap repo's scripts/make_elevation.sh and
make_terrarium.py, with GDAL command-line tools + numpy instead of rasterio.
Only tiles touching the park+forest mask (mask.npz) that the repo doesn't
already have are written."""
import json, math, os, subprocess, sys
import numpy as np
from scipy.ndimage import gaussian_filter, distance_transform_edt
from PIL import Image

W = "/private/tmp/gwnf-basemap"
E = f"{W}/elev"
REPO = f"{W}/repo"
BOX = (-80.1, 37.3, -77.75, 39.2)
mz = np.load(f"{W}/mask.npz"); MASK, MW, MN, MR = mz["m"], float(mz["W"]), float(mz["N"]), float(mz["RES"])
R = 20037508.342789244

def tile_bounds_ll(z, x, y):
    n = 2 ** z
    lon0 = x / n * 360 - 180; lon1 = (x + 1) / n * 360 - 180
    lat = lambda t: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * t / n))))
    return lon0, lat(y + 1), lon1, lat(y)

def in_mask(z, x, y):
    w, s, e, n = tile_bounds_ll(z, x, y)
    c0 = max(0, int((w - MW) / MR)); c1 = min(MASK.shape[1] - 1, int((e - MW) / MR))
    r0 = max(0, int((MN - n) / MR)); r1 = min(MASK.shape[0] - 1, int((MN - s) / MR))
    if c0 > c1 or r0 > r1: return False
    return bool(MASK[r0:r1 + 1, c0:c1 + 1].any())

OLD = (-78.95, 38.0, -77.85, 39.0)  # the park-only DEM box the repo's tiles were cut from

def inside_old(z, x, y):
    w, s, e, n = tile_bounds_ll(z, x, y)
    return w >= OLD[0] and s >= OLD[1] and e <= OLD[2] and n <= OLD[3]

def tile_range(z):
    n = 2 ** z
    x0 = int((BOX[0] + 180) / 360 * n); x1 = int((BOX[2] + 180) / 360 * n)
    ty = lambda lat: int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x0, x1, ty(BOX[3]), ty(BOX[1])

def run(*a): subprocess.run(a, check=True)

step = sys.argv[1]
os.chdir(E)
if step == "contours":
    run("gdal_translate", "-q", "-of", "EHdr", "-ot", "Float32", "dem20.tif", "dem20.bil")
    hdr = open("dem20.hdr").read()
    cols = int([l for l in hdr.splitlines() if l.startswith("NCOLS")][0].split()[1])
    rows = int([l for l in hdr.splitlines() if l.startswith("NROWS")][0].split()[1])
    order = "<" if "BYTEORDER      I" in hdr or "BYTEORDER I" in hdr else ">"
    a = np.fromfile("dem20.bil", dtype=order + "f4").reshape(rows, cols)
    a[a < -1000] = np.nan
    ft = gaussian_filter(np.nan_to_num(a, nan=float(np.nanmean(a))), sigma=1.2) * 3.28084
    ft.astype(order + "f4").tofile("dem20_ft.bil")
    for ext in ("hdr", "prj"):
        if os.path.exists(f"dem20.{ext}"):
            open(f"dem20_ft.{ext}", "w").write(open(f"dem20.{ext}").read())
    if os.path.exists("contours.geojsons"): os.remove("contours.geojsons")
    run("gdal_contour", "-q", "-a", "ele_ft", "-i", "40", "-f", "GeoJSONSeq", "dem20_ft.bil", "contours.geojsons")
    with open("contours.geojsons") as f, open("contours_tagged.geojsons", "w") as o:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line: continue
            ftr = json.loads(line); e = int(round(ftr["properties"]["ele_ft"])); idx = int(e % 200 == 0)
            ftr["properties"] = {"ele_ft": e, "idx": idx}
            ftr["tippecanoe"] = {"minzoom": 10 if idx else 12}
            o.write(json.dumps(ftr, separators=(",", ":")) + "\n")
    run("rm", "-rf", "contours_out")
    run("tippecanoe", "-q", "-e", "contours_out", "-l", "contour", "-Z10", "-z14", "--no-tile-compression",
        "--simplification=4", "--no-feature-limit", "--no-tile-size-limit", "-P", "contours_tagged.geojsons")
    added = kept = skipped = 0
    for root, _, files in os.walk("contours_out"):
        for fn in files:
            if not fn.endswith(".pbf"): continue
            rel = os.path.relpath(os.path.join(root, fn), "contours_out")
            z, x, y = (int(v) for v in rel[:-4].split("/"))
            dst = f"{REPO}/contours/{rel}"
            if os.path.exists(dst) and inside_old(z, x, y): kept += 1; continue
            if not in_mask(z, x, y) and not os.path.exists(dst): skipped += 1; continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(os.path.join(root, fn), dst); added += 1
    print("contours added", added, "already there", kept, "outside mask", skipped)
elif step == "dem":
    QUANT = 0.25
    added = kept = 0
    for z in range(8, 13):
        x0, x1, y0, y1 = tile_range(z)
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
        size = 2 * R / 2 ** z
        left, top = -R + x0 * size, R - y0 * size
        right, bottom = left + nx * size, top - ny * size
        run("gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:3857", "-te", str(left), str(bottom), str(right), str(top),
            "-ts", str(nx * 256), str(ny * 256), "-r", "bilinear", "-srcnodata", "-9999", "-dstnodata", "-9999",
            "-ot", "Float32", "-of", "EHdr", "dem20.tif", f"z{z}.bil")
        hdr = open(f"z{z}.hdr").read()
        order = "<" if "I" in [l.split()[1] for l in hdr.splitlines() if l.startswith("BYTEORDER")] else ">"
        big = np.fromfile(f"z{z}.bil", dtype=order + "f4").reshape(ny * 256, nx * 256)
        for j in range(ny):
            for i in range(nx):
                x, y = x0 + i, y0 + j
                dst = f"{REPO}/dem/{z}/{x}/{y}.png"
                if os.path.exists(dst) and inside_old(z, x, y): kept += 1; continue
                if z >= 11 and not in_mask(z, x, y) and not os.path.exists(dst): continue
                d = big[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256].astype("float32")
                miss = d < -1000
                if miss.all(): continue
                if miss.any():
                    idx = distance_transform_edt(miss, return_distances=False, return_indices=True)
                    d = d[tuple(idx)]
                h = np.round(d / QUANT) * QUANT + 32768.0
                r = np.floor(h / 256); g = np.floor(h - r * 256); bl = np.floor((h - np.floor(h)) * 256)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                Image.fromarray(np.dstack([r, g, bl]).astype("uint8")).save(dst, optimize=True)
                added += 1
        os.remove(f"z{z}.bil")
        print(f"z{z}: added so far {added}, already there {kept}", flush=True)
