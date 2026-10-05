"""Contour tiles for a new region, added to contours/ without disturbing
the tiles that are already there.

Same lines as scripts/make_elevation.sh (USGS 3DEP on a ~20 m grid,
light Gaussian smoothing, feet, 40 ft interval, index every 200 ft,
vector tiles z10-14, source-layer "contour", properties ele_ft + idx),
with the GDAL command-line tools + numpy instead of rasterio.

  python3 scripts/make_region_contours.py --name jnf-eastern-divide \\
      --region /path/to/district.geojson --work ~/mossback-work/contours-ed

--region is a GeoJSON Polygon/MultiPolygon (a geometry, Feature or
FeatureCollection; every polygon in it counts), or use --bbox W,S,E,N.
Tiles are written only where they touch the region buffered by
--buffer-km (default 3.3 km, the same 0.03 degree buffer the GW
districts got), so the tiles don't fill whole valleys.

The DEM: 3DEP 1/3 arc-second (--res 13, default; falls back to
1 arc-second where a 1/3" tile doesn't exist) or 1 arc-second
(--res 1), downloaded one 1x1 degree tile at a time into --work,
warped to the 20 m grid, then the download is deleted. --dem uses an
existing DEM instead (any GDAL raster in EPSG:4326 metres; it should
cover the buffered region). Nothing big goes into the repo.

Existing tiles that touch the new mask: if an earlier region's DEM box
covers the whole tile, it is left alone. Else, if the new DEM covers the
whole tile, it is regenerated (same source and method, so it holds every old
line, and it drops edge artefacts of the old DEM). Otherwise it is
merged: its old lines are kept, and the new lines are added only
outside every earlier region's DEM box (from
scripts/contour_regions.json), so the overlap isn't drawn twice. Tiles
that don't touch the new mask are never written. The region is then
appended to scripts/contour_regions.json; scripts/add_world_layers.py
sets the styles' contours bounds from that file, so rerun it after.

Rerunning a region that is already in contour_regions.json would
merge its lines in twice: restore contours/ from git first
(git checkout -- contours && git clean -fd contours) and pass --force.

Needs: gdal, tippecanoe, numpy, scipy, pillow, shapely, mapbox-vector-tile.
"""
import argparse, datetime, json, math, os, shutil, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, gaussian_filter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(REPO, "scripts", "contour_regions.json")
TR = 0.000185185185          # ~20 m, the grid every contour tile so far was cut from
MASK_RES = 0.002             # degrees (~200 m) for the tile mask
EDGE = 0.01               # degrees; the DEM edge is unreliable after smoothing
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
TNM = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/{r}/TIFF/current/{t}/USGS_{r}_{t}.tif"


def run(*a):
    subprocess.run([str(x) for x in a], check=True)


def tile_bounds_ll(z, x, y):
    n = 2 ** z
    lat = lambda t: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * t / n))))
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def polygons(gj):
    if gj["type"] == "FeatureCollection":
        return [p for f in gj["features"] for p in polygons(f)]
    if gj["type"] == "Feature":
        return polygons(gj["geometry"])
    if gj["type"] == "Polygon":
        return [gj["coordinates"]]
    if gj["type"] == "MultiPolygon":
        return gj["coordinates"]
    return []


def make_mask(polys, buf_deg):
    xs = [c[0] for p in polys for c in p[0]]; ys = [c[1] for p in polys for c in p[0]]
    W, S = min(xs) - buf_deg - 0.01, min(ys) - buf_deg - 0.01
    E, N = max(xs) + buf_deg + 0.01, max(ys) + buf_deg + 0.01
    nx, ny = int((E - W) / MASK_RES) + 1, int((N - S) / MASK_RES) + 1
    img = Image.new("L", (nx, ny), 0); d = ImageDraw.Draw(img)
    for p in polys:
        d.polygon([((x - W) / MASK_RES, (N - y) / MASK_RES) for x, y in p[0]], fill=1)
        for hole in p[1:]:
            d.polygon([((x - W) / MASK_RES, (N - y) / MASK_RES) for x, y in hole], fill=0)
    m = np.array(img, dtype=bool)
    r = max(1, int(round(buf_deg / MASK_RES)))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    m = binary_dilation(m, structure=(xx * xx + yy * yy) <= r * r)
    rows, cols = np.where(m)
    bbox = (W + cols.min() * MASK_RES, N - (rows.max() + 1) * MASK_RES,
            W + (cols.max() + 1) * MASK_RES, N - rows.min() * MASK_RES)
    return m, W, N, bbox


def in_mask(mask, MW, MN, z, x, y):
    w, s, e, n = tile_bounds_ll(z, x, y)
    c0 = max(0, int((w - MW) / MASK_RES)); c1 = min(mask.shape[1] - 1, int((e - MW) / MASK_RES))
    r0 = max(0, int((MN - n) / MASK_RES)); r1 = min(mask.shape[0] - 1, int((MN - s) / MASK_RES))
    if c0 > c1 or r0 > r1:
        return False
    return bool(mask[r0:r1 + 1, c0:c1 + 1].any())


def fetch_dem(work, box, res):
    """3DEP 1x1 degree tiles over box -> work/dem20.bil (ENVI, float32, 20 m grid)."""
    W, S, E, N = box
    parts = []
    for top in range(math.floor(S) + 1, math.ceil(N) + 1):
        for left in range(math.floor(W), math.ceil(E)):
            t = f"n{top:02d}w{-left:03d}"
            out = os.path.join(work, f"d20_{t}.tif")
            if not os.path.exists(out):
                src = os.path.join(work, "src.tif")
                got = None
                for r in ([res, "1"] if res == "13" else [res]):
                    rc = subprocess.run(["curl", "-sSf", "--retry", "3", "-A", UA, "-o", src,
                                         TNM.format(r=r, t=t)]).returncode
                    if rc == 0:
                        got = r; break
                if got is None:
                    print("no 3DEP tile", t, "(ocean or outside the US?) skipped"); continue
                w, s = max(left, W), max(top - 1, S)
                e, n = min(left + 1, E), min(top, N)
                run("gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:4326", "-te", w, s, e, n, "-tap",
                    "-tr", TR, TR, "-r", "average" if got == "13" else "bilinear", "-dstnodata", -9999,
                    "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", src, out)
                os.remove(src)
                print("dem", t, f"(USGS_{got})", flush=True)
            parts.append(out)
    if not parts:
        sys.exit("no DEM tiles for this box")
    vrt = os.path.join(work, "all.vrt")
    run("gdalbuildvrt", "-q", "-overwrite", "-srcnodata", -9999, "-vrtnodata", -9999, vrt, *parts)
    return vrt


def to_grid(work, src, box):
    W, S, E, N = box
    dst = os.path.join(work, "dem20.bil")
    run("gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:4326", "-te", W, S, E, N, "-tr", TR, TR,
        "-r", "bilinear", "-dstnodata", -9999, "-ot", "Float32", "-of", "ENVI", src, dst)
    return dst


def contour_lines(work, dem):
    hdr = open(dem[:-4] + ".hdr").read()
    cols = int(next(l for l in hdr.splitlines() if l.startswith("samples")).split("=")[1])
    rows = int(next(l for l in hdr.splitlines() if l.startswith("lines")).split("=")[1])
    order = ">" if "byte order = 1" in hdr else "<"
    a = np.fromfile(dem, dtype=order + "f4").reshape(rows, cols).astype("float64")
    a[a < -1000] = np.nan
    nodata = np.isnan(a)
    ft = gaussian_filter(np.nan_to_num(a, nan=float(np.nanmean(a))), sigma=1.2) * 3.28084
    ft[nodata] = -9999  # no lines where 3DEP has no data (ocean, outside the US)
    del a, nodata
    ftp = os.path.join(work, "dem20_ft.bil")
    ft.astype(order + "f4").tofile(ftp)
    del ft
    open(ftp[:-4] + ".hdr", "w").write(hdr)
    raw = os.path.join(work, "contours.geojsons")
    if os.path.exists(raw):
        os.remove(raw)
    run("gdal_contour", "-q", "-a", "ele_ft", "-i", 40, "-snodata", -9999, "-f", "GeoJSONSeq", ftp, raw)
    return raw


def tag(raw, out, protect):
    """Tag lines like make_elevation.sh; with protect, keep only the parts
    outside those boxes (earlier regions' DEMs)."""
    from shapely.geometry import box as sbox, shape, mapping
    from shapely.ops import unary_union
    cut = unary_union([sbox(*b) for b in protect]) if protect else None
    n = 0
    with open(raw) as f, open(out, "w") as o:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            ftr = json.loads(line)
            if cut is not None:
                g = shape(ftr["geometry"])
                if g.intersects(cut):
                    g = g.difference(cut)
                    if g.is_empty:
                        continue
                    ftr["geometry"] = mapping(g)
            e = int(round(ftr["properties"]["ele_ft"])); idx = int(e % 200 == 0)
            ftr["properties"] = {"ele_ft": e, "idx": idx}
            ftr["tippecanoe"] = {"minzoom": 10 if idx else 12}
            o.write(json.dumps(ftr, separators=(",", ":")) + "\n"); n += 1
    return n


def tippecanoe(src, out):
    shutil.rmtree(out, ignore_errors=True)
    run("tippecanoe", "-q", "-e", out, "-l", "contour", "-Z10", "-z14", "--no-tile-compression",
        "--simplification=4", "--no-feature-limit", "--no-tile-size-limit", "-P", src)


def merge_tile(old_path, new_path):
    import mapbox_vector_tile as mvt
    opts = {"y_coord_down": True}
    layers = {}
    for p in (old_path, new_path):
        t = mvt.decode(open(p, "rb").read(), default_options=opts)
        for name, lay in t.items():
            L = layers.setdefault(name, {"name": name, "features": [], "extent": lay.get("extent", 4096)})
            for f in lay["features"]:
                L["features"].append({"geometry": f["geometry"], "properties": f["properties"]})
    data = b"".join(
        mvt.encode([L], default_options={**opts, "extents": L["extent"]}) for L in layers.values())
    open(old_path, "wb").write(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="short id for contour_regions.json")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--region", help="GeoJSON polygon(s)")
    g.add_argument("--bbox", help="W,S,E,N")
    ap.add_argument("--work", required=True, help="working dir outside the repo")
    ap.add_argument("--buffer-km", type=float, default=3.3)
    ap.add_argument("--res", choices=["13", "1"], default="13", help="3DEP 1/3 (13) or 1 arc-second")
    ap.add_argument("--dem", help="use this DEM instead of downloading")
    ap.add_argument("--repo", default=REPO, help="basemap repo (default: this one)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    work = os.path.abspath(os.path.expanduser(a.work)); os.makedirs(work, exist_ok=True)
    registry = os.path.join(a.repo, "scripts", "contour_regions.json")
    regions = json.load(open(registry)) if os.path.exists(registry) else []
    if any(r["name"] == a.name for r in regions):
        if not a.force:
            sys.exit(f"{a.name} is already in contour_regions.json (see the rerun note at the top)")
        regions = [r for r in regions if r["name"] != a.name]
    buf = a.buffer_km / 111.0
    if a.region:
        polys = polygons(json.load(open(a.region)))
    else:
        W, S, E, N = (float(v) for v in a.bbox.split(","))
        polys = [[[[W, S], [E, S], [E, N], [W, N], [W, S]]]]
    if not polys:
        sys.exit("no polygons in the region")
    mask, MW, MN, mb = make_mask(polys, buf)
    # DEM box: the buffered region plus a margin, snapped to 0.01 degree.
    box = (math.floor((mb[0] - 0.01) * 100) / 100, math.floor((mb[1] - 0.01) * 100) / 100,
           math.ceil((mb[2] + 0.01) * 100) / 100, math.ceil((mb[3] + 0.01) * 100) / 100)
    print("mask bbox", [round(float(v), 4) for v in mb], "DEM box", box, flush=True)

    src = a.dem or fetch_dem(work, box, a.res)
    dem = to_grid(work, src, box)
    raw = contour_lines(work, dem)
    protect = [r["dem_bbox"] for r in regions
               if not (r["dem_bbox"][2] <= box[0] or r["dem_bbox"][0] >= box[2]
                       or r["dem_bbox"][3] <= box[1] or r["dem_bbox"][1] >= box[3])]
    full_in = os.path.join(work, "tagged_full.geojsons")
    print("lines", tag(raw, full_in, []), flush=True)
    tippecanoe(full_in, os.path.join(work, "out_full"))
    clip_dir = os.path.join(work, "out_clip")
    shutil.rmtree(clip_dir, ignore_errors=True)
    if protect:
        clip_in = os.path.join(work, "tagged_clip.geojsons")
        print("lines outside earlier regions", tag(raw, clip_in, protect), flush=True)
        tippecanoe(clip_in, clip_dir)

    dst_root = os.path.join(a.repo, "contours")
    added = merged = replaced = kept = skipped = 0
    full_dir = os.path.join(work, "out_full")
    for root, _, files in os.walk(full_dir):
        for fn in files:
            if not fn.endswith(".pbf"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), full_dir)
            z, x, y = (int(v) for v in rel[:-4].split("/"))
            if not in_mask(mask, MW, MN, z, x, y):
                skipped += 1; continue
            dst = os.path.join(dst_root, rel)
            if os.path.exists(dst):
                w, s, e, n = tile_bounds_ll(z, x, y)
                if any(w >= b[0] + EDGE and s >= b[1] + EDGE and e <= b[2] - EDGE and n <= b[3] - EDGE
                       for b in protect):
                    kept += 1; continue  # already complete from an earlier region
                if w >= box[0] + EDGE and s >= box[1] + EDGE and e <= box[2] - EDGE and n <= box[3] - EDGE:
                    # The new DEM covers the whole tile: the new tile already
                    # has every line the old one had (same 3DEP grid, same
                    # method) and none of the old DEM's edge artefacts.
                    shutil.copyfile(os.path.join(root, fn), dst); replaced += 1
                    continue
                clip = os.path.join(clip_dir, rel)
                if os.path.exists(clip):
                    merge_tile(dst, clip); merged += 1
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(root, fn), dst); added += 1
    print(f"contours: added {added}, existing merged {merged}, existing regenerated {replaced}, existing kept {kept}, outside mask {skipped}")

    regions.append({"name": a.name, "dem_bbox": list(box), "mask_bbox": [round(float(v), 4) for v in mb],
                    "buffer_km": a.buffer_km, "source": "USGS 3DEP", "added": str(datetime.date.today()),
                    "tiles_added": added, "tiles_merged": merged,
                    "tiles_regenerated": replaced})
    json.dump(regions, open(registry, "w"), indent=2); open(registry, "a").write("\n")
    print("registry", registry, "now", len(regions), "regions; rerun scripts/add_world_layers.py")


if __name__ == "__main__":
    main()
