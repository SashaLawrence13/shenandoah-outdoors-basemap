#!/bin/sh
# Publish the map files to Cloudflare Pages (free; no GitHub Pages
# 100 GB/month cap or per-client 429s). Two sites because a free Pages
# site holds at most 20,000 files:
#   mossback-tiles  tiles/                     (OpenMapTiles vector tiles)
#   mossback-maps   contours/ dem/ glyphs/ and the style JSONs, rewritten
#                   to point at both sites.
# Needs wrangler logged in (`wrangler login`). Run from the repo root:
#   sh scripts/deploy_cloudflare.sh /path/to/node_modules/.bin/wrangler
# Big uploads can drop the connection ("fetch failed"); rerunning only
# sends what's missing, and uploading a folder at a time (a preview
# branch per folder) gets around it.
set -e
WRANGLER=${1:-wrangler}
STAGE=${STAGE:-/tmp/cf-stage}
GH=https://sashalawrence13.github.io/shenandoah-outdoors-basemap
TH=https://mossback-tiles.pages.dev
MH=https://mossback-maps.pages.dev
rm -rf "$STAGE" && mkdir -p "$STAGE/tiles" "$STAGE/maps"
cp -R tiles "$STAGE/tiles/tiles"
cp -R contours dem glyphs "$STAGE/maps/"
for f in outdoors.json topo.json satellite.json usgs-topo.json; do
  sed -e "s#$GH/tiles/#$TH/tiles/#g" -e "s#$GH/#$MH/#g" "$f" > "$STAGE/maps/$f"
done
printf '/*\n  Access-Control-Allow-Origin: *\n  Cache-Control: public, max-age=86400\n/*.pbf\n  Content-Type: application/x-protobuf\n' > "$STAGE/tiles/_headers"
cp "$STAGE/tiles/_headers" "$STAGE/maps/_headers"
"$WRANGLER" pages deploy "$STAGE/tiles" --project-name mossback-tiles --branch main --commit-dirty=true
"$WRANGLER" pages deploy "$STAGE/maps" --project-name mossback-maps --branch main --commit-dirty=true
