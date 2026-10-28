#!/usr/bin/env python3
"""EPA AirNow for all of the U.S.: reporting areas as 2 degree tiles (Maps lane, docs/AIR_NATIONAL.md).

Source: AirNow's national file product, no key, refreshed hourly:
https://files.airnowtech.org/airnow/today/reportingarea.dat
One bar-separated row per area, pollutant and day: issue date, valid date, valid time, time zone,
record sequence (-1 yesterday, 0 today, 1..5 ahead), type (O observed, F forecast, Y yesterday),
primary flag, area, state, latitude, longitude, pollutant, AQI, category, action day, discussion, agency.

Writes, under --out (default conditions/air beside this repo's other conditions files):
- <lonIndex>_<latIndex>.json: the areas whose point falls in that 2 degree tile (index = floor(degrees / 2)),
  each with its current worst-pollutant reading `o`, up to three days of worst-pollutant forecasts `f`
  and the forecast discussion `t` (first 300 characters). The app reads it in src/features/conditions/airNational.ts.
- index.json: {"fetchedAt", "source", "tiles": [names], "etag", "lastModified"} so a missing tile reads as
  "no reporting area here"; the last two let the next run ask the server whether the file changed.

Refresh window: nothing is fetched when index.json is under MIN_AGE_MINUTES old, and the download is
conditional (If-None-Match / If-Modified-Since), so an unchanged hourly file costs one empty 304 reply and
leaves every tile (and its fetchedAt) as it was. Any failure keeps the last good tiles and exits 0.
Tile files no longer in the index are removed so the folder matches it.

Data are preliminary; the app says "EPA AirNow" and so should anything that shows them.
Run by .github/workflows/alerts.yml (basemap repo). Tests: python3 -m unittest test_fetch_air_national
"""
import argparse, glob, json, math, os, re, sys, urllib.error, urllib.request
from datetime import datetime, timezone

URL = "https://files.airnowtech.org/airnow/today/reportingarea.dat"
UA = "Mossback conditions (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "conditions", "air")
FORECAST_DAYS = 3  # record sequence 0, 1, 2
DISCUSSION_CHARS = 300
TILE_DEG = 2
MIN_AGE_MINUTES = 50  # the source changes hourly; the workflow runs every 3 hours
MIN_AREAS = 300  # fewer means the file is broken; keep the old tiles


def download(url=URL, timeout=60, etag=None, last_modified=None):
    """(text, etag, last_modified), or None when the server says the file has not changed."""
    headers = {"User-Agent": UA}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None
        raise
    return r.read().decode("latin-1"), r.headers.get("ETag"), r.headers.get("Last-Modified")


def parse(text):
    """Areas keyed by (name, state) with the app's fields; rows that do not parse are skipped."""
    areas = {}
    for line in text.splitlines():
        f = line.rstrip("\n").split("|")
        if len(f) < 17:
            continue
        issue, valid, vtime, tz, seq, typ, _prim, name, state, lat, lon, pol, aqi, cat, action, disc, agency = f[:17]
        state = state.strip()
        if typ == "Y" or not name or not state or state == "MX":
            continue
        try:
            lat, lon, seq, aqi = float(lat), float(lon), int(seq), int(aqi)
        except ValueError:
            continue
        if aqi < 0:
            continue
        a = areas.setdefault((name, state), {"n": name, "s": state, "la": lat, "lo": lon, "ag": agency,
                                             "tz": tz, "obsDate": "", "o": None, "fall": {}, "t": ""})
        if typ == "O" and seq == 0:
            if a["o"] is None or aqi > a["o"][1]:
                a["o"] = [pol, aqi, cat, vtime]
                a["obsDate"] = valid
                a["tz"] = tz
        elif typ == "F" and 0 <= seq < FORECAST_DAYS:
            cur = a["fall"].get(valid)
            if cur is None or aqi > cur[2]:
                a["fall"][valid] = [valid, pol, aqi, cat, action == "Yes"]
        if disc.strip() and not a["t"]:
            a["t"] = disc.strip()[:DISCUSSION_CHARS]
    out = []
    for a in areas.values():
        a["f"] = sorted(a.pop("fall").values(), key=lambda x: datetime.strptime(x[0], "%m/%d/%y"))
        if a["o"] or a["f"]:
            out.append(a)
    return out


def tile_name(lat, lon):
    return f"{math.floor(lon / TILE_DEG)}_{math.floor(lat / TILE_DEG)}"


def tiles_of(areas):
    tiles = {}
    for a in areas:
        tiles.setdefault(tile_name(a["la"], a["lo"]), []).append(a)
    for items in tiles.values():
        items.sort(key=lambda a: (a["s"], a["n"]))
    return tiles


def read_index(out):
    try:
        with open(os.path.join(out, "index.json")) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def fresh(index, now, minutes=MIN_AGE_MINUTES):
    try:
        at = datetime.fromisoformat(index["fetchedAt"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return False
    return (now - at).total_seconds() < minutes * 60


def write(tiles, out, now, etag=None, last_modified=None):
    os.makedirs(out, exist_ok=True)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for name, items in tiles.items():
        with open(os.path.join(out, f"{name}.json"), "w") as fh:
            json.dump({"fetchedAt": stamp, "source": "airnow-files", "areas": items}, fh, separators=(",", ":"))
    for path in glob.glob(os.path.join(out, "*.json")):
        name = os.path.basename(path)[:-5]
        if re.fullmatch(r"-?\d+_-?\d+", name) and name not in tiles:
            os.remove(path)
    index = {"fetchedAt": stamp, "source": "airnow-files", "tiles": sorted(tiles)}
    if etag:
        index["etag"] = etag
    if last_modified:
        index["lastModified"] = last_modified
    with open(os.path.join(out, "index.json"), "w") as fh:
        json.dump(index, fh, separators=(",", ":"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--from-file", help="read a saved copy of the file instead of downloading")
    ap.add_argument("--force", action="store_true", help="ignore the refresh window and the server's 304")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    index = read_index(args.out)
    etag = last_modified = None
    if args.from_file:
        text = open(args.from_file, encoding="latin-1").read()
    else:
        if not args.force and fresh(index, now):
            print(f"air: index is under {MIN_AGE_MINUTES} minutes old; nothing fetched")
            return 0
        try:
            got = download(etag=None if args.force else index.get("etag"),
                           last_modified=None if args.force else index.get("lastModified"))
        except Exception as e:  # keep the last good tiles; the URL carries no key, but say only the error type
            print(f"air: download failed ({type(e).__name__}); last good tiles kept")
            return 0
        if got is None:
            print("air: source unchanged since the last run (304); tiles kept")
            return 0
        text, etag, last_modified = got
    areas = parse(text)
    if len(areas) < MIN_AREAS:
        print(f"air: only {len(areas)} reporting areas parsed; the file looks wrong, last good tiles kept")
        return 0
    tiles = tiles_of(areas)
    write(tiles, args.out, now, etag, last_modified)
    print(f"air: {len(areas)} areas in {len(tiles)} tiles -> {os.path.normpath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
