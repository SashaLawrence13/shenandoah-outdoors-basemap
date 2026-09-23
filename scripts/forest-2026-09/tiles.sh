#!/bin/zsh
# Vector tiles for Shenandoah + George Washington NF (Lee, North River, Glenwood-Pedlar).
set -e
BOX=-80.1,37.3,-77.75,39.2
W=/private/tmp/gwnf-basemap
B=/private/tmp/basemap-build
cd $W
[ -f west-virginia-latest.osm.pbf ] || curl -sSfL -o west-virginia-latest.osm.pbf https://download.geofabrik.de/north-america/us/west-virginia-latest.osm.pbf
osmium extract -b $BOX $B/virginia-latest.osm.pbf -o va.osm.pbf --overwrite
osmium extract -b $BOX west-virginia-latest.osm.pbf -o wv.osm.pbf --overwrite
osmium merge va.osm.pbf wv.osm.pbf -o region.osm.pbf --overwrite
ls -la region.osm.pbf
cd $B   # reuse data/sources (Natural Earth, water polygons, lake centerlines)
/opt/homebrew/opt/openjdk/bin/java -Xmx6g -jar planetiler.jar --osm-path=$W/region.osm.pbf \
  --output=$W/region.mbtiles --force --tile_compression=none
echo TILES_DONE
