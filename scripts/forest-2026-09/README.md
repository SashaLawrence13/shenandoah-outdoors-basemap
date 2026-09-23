# Forest extension of the basemap (2026-09-23)

How the self-hosted tiles were widened from Shenandoah alone to Shenandoah
plus George Washington National Forest's Lee, North River and
Glenwood-Pedlar districts. Run in this order, from a colon-free working
folder (the paths inside point at `/private/tmp/gwnf-basemap`; change them
if you rebuild elsewhere):

1. `tiles.sh`: Virginia + West Virginia OSM extracts (Geofabrik), clipped
   to -80.1,37.3,-77.75,39.2 and merged, then Planetiler. Lee and North
   River reach into West Virginia, hence the second extract.
2. `elev.sh`: nine USGS 3DEP 1/3 arc-second tiles, each resampled to
   ~20 m and cropped to the box, then mosaicked (`dem20.tif`).
3. `mask.py`: the park boundary plus the three district polygons,
   buffered ~3 km. Contour and hillshade tiles are only written where
   they touch it, so the valleys between aren't filled.
4. `contours_dem.py dem`, then `contours_dem.py contours`: the same
   method as `../make_elevation.sh` and `../make_terrarium.py` (40 ft
   contours, index every 200 ft; terrarium PNGs z8-12), using GDAL's
   command-line tools and numpy because rasterio wasn't installed. Park
   tiles that sat wholly inside the old park-only box are kept; tiles
   that straddled its edge (padded outward then) are rebuilt.
5. `push_batches.py`: commits and pushes in ~3.5 MB batches, retrying,
   because bigger pushes from this Mac fail with "inflate" errors.

Tools: Homebrew's OpenJDK (for Planetiler), osmium, GDAL and tippecanoe,
all already installed on the build Mac. Result: 18,224 vector tiles
(11,359 new) and 3,311 new or rebuilt contour and elevation tiles.
