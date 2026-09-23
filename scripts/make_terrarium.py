"""Terrarium-encoded raster-dem tiles (256 px PNG) from the park DEM."""
import os, sys, numpy as np, mercantile, rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
from scipy.ndimage import distance_transform_edt
from PIL import Image

SRC = "work/dem20.tif"
OUT = "dem"
PARK = (-78.9, 38.03, -77.9, 39.0)   # src/config/parks/shenandoah.ts bounds
ZOOMS = range(int(sys.argv[1]), int(sys.argv[2]) + 1)
QUANT = 0.25  # metres; keeps the low byte compressible

src = rasterio.open(SRC)
data = src.read(1)
n = 0; total = 0
for z in ZOOMS:
    for t in mercantile.tiles(*PARK, zooms=z):
        b = mercantile.xy_bounds(t)
        dst = np.full((256, 256), np.nan, dtype="float32")
        reproject(data, dst, src_transform=src.transform, src_crs=src.crs,
                  src_nodata=src.nodata,
                  dst_transform=from_bounds(b.left, b.bottom, b.right, b.top, 256, 256),
                  dst_crs="EPSG:3857", dst_nodata=np.nan, resampling=Resampling.bilinear)
        miss = np.isnan(dst)
        if miss.all():
            continue
        if miss.any():
            idx = distance_transform_edt(miss, return_distances=False, return_indices=True)
            dst = dst[tuple(idx)]
        h = np.round(dst / QUANT) * QUANT + 32768.0
        r = np.floor(h / 256); g = np.floor(h - r * 256); bl = np.floor((h - np.floor(h)) * 256)
        img = np.dstack([r, g, bl]).astype("uint8")
        path = f"{OUT}/{z}/{t.x}"
        os.makedirs(path, exist_ok=True)
        fn = f"{path}/{t.y}.png"
        Image.fromarray(img).save(fn, optimize=True)
        n += 1; total += os.path.getsize(fn)
    print(f"z{z} done: {n} tiles, {total/1e6:.1f} MB", flush=True)
