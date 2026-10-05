# Shenandoah Outdoors — self-hosted basemap

Vector map tiles for the [Shenandoah Outdoors app](https://github.com/SashaLawrence13/shenandoah-outdoors),
generated and hosted here rather than pulled from a third-party tile
service — so there's no ambiguity about whether the app's offline
download pack is allowed to cache and redistribute this data.

## What this is

- **Source data**: OpenStreetMap, via a [Geofabrik](https://download.geofabrik.de/)
  Virginia and West Virginia extracts, clipped to a box covering
  Shenandoah National Park and George Washington National Forest's Lee,
  North River and Glenwood-Pedlar districts, plus the roads and towns
  around them, with [osmium-tool](https://osmcode.org/osmium-tool/).
- **Tile generation**: [Planetiler](https://github.com/onthegomap/planetiler)
  with its default OpenMapTiles-schema profile, producing standard
  `{z}/{x}/{y}.pbf` vector tiles, zoom 0–14.
- **Style**: `style.json` — a small, deliberately label-free style (no
  `glyphs`/`sprite`, no `symbol` layers). The app draws its own trail
  names, POI markers and labels as native UI on top of the map; the
  basemap's job is background context (water, land cover, roads), not
  text, so there was nothing worth generating a font/glyph pipeline for.
- **More styles** (added 2026-09-22), all static files here:
  - `topo.json` — Topo: the same vector tiles plus hillshading
    (`dem/`, terrarium-encoded elevation tiles, z8–12) and 40 ft contour
    lines (`contours/`, vector tiles z10–14, index lines every 200 ft),
    both generated from the USGS 3DEP 1/3 arc-second DEM (public domain),
    with peak, place, stream and road names.
  - `satellite.json` — USGS National Map imagery (USDA NAIP, public
    domain), served by `basemap.nationalmap.gov`, with our names on top.
  - `usgs-topo.json` — the USGS National Map's own topo map, served by
    `basemap.nationalmap.gov` (public domain).
  - `glyphs/` — a Latin-only cut of Noto Sans (SIL Open Font License,
    `glyphs/OFL.txt`) for those labels. Every range a style could ask for
    exists (the unneeded ones are empty), so an offline download is ~1 MB
    of fonts rather than ~100 MB.
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
curl -L -o west-virginia-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/west-virginia-latest.osm.pbf

# Bounding box: Shenandoah NP plus George Washington National Forest's
# Lee, North River and Glenwood-Pedlar districts (widened 2026-09-23).
# Lee and North River reach into West Virginia, hence the second extract.
osmium extract -b -80.1,37.3,-77.75,39.2 virginia-latest.osm.pbf -o va.osm.pbf --overwrite
osmium extract -b -80.1,37.3,-77.75,39.2 west-virginia-latest.osm.pbf -o wv.osm.pbf --overwrite
osmium merge va.osm.pbf wv.osm.pbf -o region.osm.pbf --overwrite

curl -L -o planetiler.jar \
  https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar
java -jar planetiler.jar --osm-path=region.osm.pbf \
  --output=shenandoah.mbtiles --force --download

mb-util --image_format=pbf shenandoah.mbtiles tiles/

# Planetiler/mb-util output is gzip-compressed .pbf — decompress in place
# so any plain static host works with zero server configuration (no
# Content-Encoding header needed).
find tiles -name "*.pbf" -print0 | \
  xargs -0 -I{} sh -c 'gunzip -c "{}" > "{}.tmp" && mv "{}.tmp" "{}"'
```

The Topo additions:

```bash
brew install gdal tippecanoe
pip3 install rasterio numpy scipy mercantile pillow
scripts/make_elevation.sh            # contours/ and dem/ from USGS 3DEP
python3 scripts/trim_glyphs.py fonts glyphs   # see the script's docstring
python3 scripts/make_styles.py https://sashalawrence13.github.io/shenandoah-outdoors-basemap .
```

Re-run this whenever the app expands to a new region — widen the
`osmium extract` bounding box and regenerate rather than maintaining a
second tileset. The contour and elevation tiles (`contours/`, `dem/`)
are built by `scripts/make_elevation.sh`; since the forest extension
they cover the park and the three forest districts plus about 3 km, not
the whole box.

## Cloudflare Pages (the app's map host since 2026-09-24)

Mossback's builds from 2026-09-24 load maps from Cloudflare Pages instead of
this GitHub Pages site: `https://mossback-tiles.pages.dev` (the `tiles/`
folder) and `https://mossback-maps.pages.dev` (contours, elevation tiles,
glyphs and the four style files, rewritten to point at both). GitHub Pages
has a 100 GB/month soft limit and throttles heavy clients with HTTP 429;
Cloudflare Pages' free plan has no bandwidth cap but holds 20,000 files per
site, hence two sites. `scripts/deploy_cloudflare.sh` rebuilds and uploads
both from this repo. This site keeps serving older builds and the live data
files (alerts, park, conditions, ridb), which the scheduled job rewrites.

## Adding contours for a new region

Outside the original box the styles draw OpenFreeMap and AWS terrain, but
contours only exist where we have built them. To add a region (a park,
a forest district):

```bash
brew install gdal tippecanoe
pip3 install numpy scipy pillow shapely mapbox-vector-tile
python3 scripts/make_region_contours.py --name jnf-eastern-divide \
  --region district.geojson --work ~/contours-work/eastern-divide
# or --bbox=W,S,E,N instead of --region
python3 scripts/add_world_layers.py outdoors.json topo.json satellite.json
sh scripts/deploy_cloudflare.sh /path/to/wrangler
```

The script downloads USGS 3DEP 1/3 arc-second tiles (1 arc-second where
there is no 1/3; `--res 1` for 1 arc-second only) into the working
directory, never the repo, and makes the same lines as the rest of
`contours/`: 40 ft interval, index lines every 200 ft (`idx` = 1) from
z10, the rest from z12, z10 to 14, source-layer `contour`, properties
`ele_ft` and `idx`. It writes only tiles that touch the region plus
`--buffer-km` (3.3 by default, as for the GW districts). Tiles already in
`contours/` that an earlier region fully covers are left alone; tiles
the new DEM fully covers are regenerated (same source and method);
other overlapping tiles keep their old lines and gain the new ones from
outside the earlier regions' DEM boxes. Each run is recorded in
`scripts/contour_regions.json`, and `add_world_layers.py` sets the
styles' `contours` bounds to one bbox over every region there. To redo a
region, restore `contours/` from git and pass `--force`.

Regions so far: Shenandoah NP and the GW districts (original box), and
the Jefferson NF Eastern Divide district (2026-10-05, 2,032 tiles added,
2 regenerated, about 24 MB); later the Blue Ridge Parkway corridor, Warm Springs, James River and
Mount Rogers; and (2026-10-05) the Potomac cluster (C&O Canal, Great Falls,
Harpers Ferry, Catoctin, Prince William Forest, Sky Meadows, Manassas: 1,562
tiles added) and the Monongahela highlands (1,826 tiles added), both masked
to the app's trails + 3.3 km. Big regions need many GB of scratch space: use
a RAM disk for `--work` if the disk is tight (`--dem` takes an existing
DEM, and the script now deletes big intermediates as it goes).

File budget: a free Cloudflare Pages site holds 20,000 files and 25 MiB
per file. mossback-maps had about 9,980 files after Eastern Divide (16,230 after Potomac and Monongahela)
(contours, dem, glyphs, styles). Count before deploying
(`find contours dem glyphs -type f | wc -l`). When mossback-maps nears
about 18,000 files, start a second Pages project for the new region
(one free Pages site per region, decided 2026-09-24) and point a second
contours source at it rather than adding more files here.
mossback-tiles is already near its cap; don't add files there.
