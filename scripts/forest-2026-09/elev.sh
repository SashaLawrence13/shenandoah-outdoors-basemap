#!/bin/zsh
# Contours and terrarium DEM for the Forest box (USGS 3DEP 1/3 arc-second, public domain).
# Same method as the park's make_elevation.sh, without rasterio/mercantile.
set -e
cd /private/tmp/gwnf-basemap/elev
TR=0.000185185185
for t in n38w080 n39w080 n40w080 n38w079 n39w079 n40w079 n38w078 n39w078 n40w078; do
  [ -f d20_$t.tif ] && continue
  read w s e n <<< $(python3 -c "t='$t'; la=int(t[1:3]); lo=int(t[4:7]); print(max(-lo,-80.1), max(la-1,37.3), min(-lo+1,-77.75), min(la,39.2))")
  curl -sSf -o src.tif "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/$t/USGS_13_$t.tif"
  gdalwarp -q -overwrite -t_srs EPSG:4326 -te $w $s $e $n -tap -tr $TR $TR -r average -dstnodata -9999 -co COMPRESS=DEFLATE -co TILED=YES src.tif d20_$t.tif
  rm -f src.tif; echo "dem $t"
done
gdalbuildvrt -q -srcnodata -9999 -vrtnodata -9999 all.vrt d20_*.tif
gdalwarp -q -overwrite -te -80.1 37.3 -77.75 39.2 -tr $TR $TR -dstnodata -9999 -co COMPRESS=DEFLATE -co TILED=YES all.vrt dem20.tif
echo DEM_DONE
