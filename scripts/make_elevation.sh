#!/bin/zsh
# Contour tiles (contours/) and terrarium elevation tiles (dem/) for the
# Topo style, from the USGS 3DEP 1/3 arc-second DEM (public domain).
# Needs: brew install gdal tippecanoe; pip install rasterio numpy scipy mercantile pillow
set -e
mkdir -p work && cd work
for t in n39w079 n39w078; do
  [ -f USGS_13_$t.tif ] || curl -sSfO "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/$t/USGS_13_$t.tif"
done
gdalbuildvrt -q mosaic.vrt USGS_13_n39w079.tif USGS_13_n39w078.tif
# ~20 m, cropped to Shenandoah's bounds plus a margin.
gdalwarp -q -overwrite -t_srs EPSG:4326 -te -78.95 38.0 -77.85 39.0 \
  -tr 0.000185185185 0.000185185185 -r average -co COMPRESS=DEFLATE -co TILED=YES mosaic.vrt dem20.tif

# Contours: smooth lightly (so 40 ft lines don't stair-step), feet, 40 ft interval.
python3 - <<'PY'
import rasterio, numpy as np
from scipy.ndimage import gaussian_filter
with rasterio.open("dem20.tif") as d:
    a = d.read(1).astype("float32"); prof = d.profile
a[a < -1000] = np.nan
ft = gaussian_filter(np.nan_to_num(a, nan=float(np.nanmean(a))), sigma=1.2) * 3.28084
prof.update(nodata=None, dtype="float32")
with rasterio.open("dem20_ft_smooth.tif", "w", **prof) as o: o.write(ft, 1)
PY
rm -f contours.geojsons && gdal_contour -q -a ele_ft -i 40 -f GeoJSONSeq dem20_ft_smooth.tif contours.geojsons
# Index lines (every 200 ft) from z10, the rest from z12.
python3 - <<'PY'
import json
with open("contours.geojsons") as f, open("contours_tagged.geojsons", "w") as o:
    for line in f:
        line = line.strip().lstrip("\x1e")
        if not line: continue
        ft = json.loads(line); e = int(round(ft["properties"]["ele_ft"])); idx = int(e % 200 == 0)
        ft["properties"] = {"ele_ft": e, "idx": idx}
        ft["tippecanoe"] = {"minzoom": 10 if idx else 12}
        o.write(json.dumps(ft, separators=(",", ":")) + "\n")
PY
rm -rf ../contours && tippecanoe -q -e ../contours -l contour -Z10 -z14 --no-tile-compression \
  --simplification=4 --no-feature-limit --no-tile-size-limit -P contours_tagged.geojsons

# Elevation tiles for hillshading, z8-12.
rm -rf ../dem && (cd .. && python3 scripts/make_terrarium.py 8 12)
