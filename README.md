# Shenandoah Outdoors — self-hosted basemap

Vector map tiles for the [Shenandoah Outdoors app](https://github.com/SashaLawrence13/shenandoah-outdoors),
generated and hosted here rather than pulled from a third-party tile
service — so there's no ambiguity about whether the app's offline
download pack is allowed to cache and redistribute this data.

## What this is

- **Source data**: OpenStreetMap, via a [Geofabrik](https://download.geofabrik.de/)
  Virginia extract, clipped to a box covering Shenandoah National Park
  plus a buffer (roads/towns near trailheads, and the Charlottesville
  area) with [osmium-tool](https://osmcode.org/osmium-tool/).
- **Tile generation**: [Planetiler](https://github.com/onthegomap/planetiler)
  with its default OpenMapTiles-schema profile, producing standard
  `{z}/{x}/{y}.pbf` vector tiles, zoom 0–14.
- **Style**: `style.json` — a small, deliberately label-free style (no
  `glyphs`/`sprite`, no `symbol` layers). The app draws its own trail
  names, POI markers and labels as native UI on top of the map; the
  basemap's job is background context (water, land cover, roads), not
  text, so there was nothing worth generating a font/glyph pipeline for.
- **Hosting**: static files, served via GitHub Pages. No tile server,
  no backend — matches the main app's own "no custom backend" default.

## License and attribution

Per [Planetiler's own notice](https://github.com/onthegomap/planetiler)
and the [OpenMapTiles project](https://github.com/openmaptiles/openmaptiles/#license):
tiles produced this way from OpenStreetMap data are reusable under
**CC-BY**, provided any map made from them carries a visible credit.
The Shenandoah Outdoors app shows exactly this attribution on every map
screen:

> © OpenMapTiles © OpenStreetMap contributors

OpenStreetMap's own data is [ODbL](https://www.openstreetmap.org/copyright).

## Regenerating

```bash
brew install openjdk osmium-tool
pip3 install mbutil

curl -L -o virginia-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/virginia-latest.osm.pbf

# Bounding box: Shenandoah NP's own bounds (see src/config/parks/shenandoah.ts)
# plus a buffer for trailhead access roads and the Charlottesville area.
osmium extract -b -79.05,37.9,-77.75,39.15 \
  virginia-latest.osm.pbf -o shenandoah-region.osm.pbf --overwrite

curl -L -o planetiler.jar \
  https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar
java -jar planetiler.jar --osm-path=shenandoah-region.osm.pbf \
  --output=shenandoah.mbtiles --force --download

mb-util --image_format=pbf shenandoah.mbtiles tiles/

# Planetiler/mb-util output is gzip-compressed .pbf — decompress in place
# so any plain static host works with zero server configuration (no
# Content-Encoding header needed).
find tiles -name "*.pbf" -print0 | \
  xargs -0 -I{} sh -c 'gunzip -c "{}" > "{}.tmp" && mv "{}.tmp" "{}"'
```

Re-run this whenever the app expands to a new region (e.g. George
Washington National Forest) — widen the `osmium extract` bounding box
and regenerate rather than maintaining a second tileset.
