#!/usr/bin/env python3
"""Live conditions for the Mossback app, published on this repo's Pages (the keys stay in this
repo's Actions secrets):

- conditions/conditions.json: satellite fire detections for every region; air quality and
  recent birds for the regions the app's older builds read from this file (the `mainFile`
  regions: Shenandoah, the forest, the Parkway, Potomac, Monongahela, Smokies, New River and
  the Pisgah); VDOT road-weather stations and traffic.
- conditions/regions/<id>.json: one small file per region with its air readings and forecasts
  and its eBird list, so a phone downloads only the area it shows. The main file's
  `regionFiles` lists the regions whose air and birds are ONLY in their own file.

Which regions there are, their boxes, where their centers are (air and birds are read there)
and how far the fire check reaches around each come from conditions_regions.json beside this
script, exported from the app's registry (src/config/regions.ts) by
src/features/conditions/conditionsRegions.test.ts (plan 3.5, 2026-10-07): there is no
hand-written list of places here any more. Add a region to the registry, copy the new JSON
here, and the job covers it on its next run.

- fires: NASA FIRMS active-fire detections (VIIRS on Suomi NPP, NOAA-20 and NOAA-21, and
  MODIS), last 2 days, queried as a few rectangles that together cover every region's fire
  box (its data box padded by 60 miles or more) and the one box older builds read
  (`fires.box`). Kept: detections inside any of those boxes, each tagged with its nearest
  region. `fires.boxes` lists the boxes a query answered for, which is the only ground the app
  may call "no fires". A region with more than FIRE_CAP detections is thinned to one per
  0.05-degree cell and day and then to the nearest to its data box (`thinned`, `truncated`).
  Key: FIRMS_MAP_KEY. Public domain (NASA). Low-confidence detections are dropped.
- air: EPA AirNow current observations and today's/tomorrow's forecasts near each center.
  AirNow allows about 500 requests an hour per key and a center costs 3, so each run reads a
  third of the regions (md5(id) % 3 == run % 3, 8 runs a day). Key: AIRNOW_API_KEY. AirNow
  data is preliminary; attribution to AirNow. Where AirNow does not answer (its key was
  refused with HTTP 410 on 2026-10-05, so that is every center today) Open-Meteo's modeled
  US AQI (Copernicus CAMS, CC BY 4.0, no key; one batched call per 40 centers) fills in,
  marked source "open-meteo" on each reading.
- birds: eBird recent (last 7 days) and notable sightings around each region's centers.
  Shenandoah's and the forest's keep the old birds.park and birds.forest keys; every other
  region's list is birds.byRegion.<id> in the main file (mainFile regions only) and
  birds.list in the region's file. Key: EBIRD_API_KEY. Observations at private locations are
  left out; eBird already hides sensitive species.
- roads: VDOT SmarterRoads road-weather stations (RWIS) on the passes up to the park and the
  forest: air and pavement temperature, visibility (fog), precipitation. Key: VDOT_TOKEN
  (the owner's SmarterRoads token).
Run by .github/workflows/alerts.yml with the alerts. Tests: python3 -m unittest test_fetch_conditions
(in this folder, in the app repo's data-pipeline/alerts/).
"""
import csv, hashlib, io, json, math, os, sys, time, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone

UA = "Mossback conditions (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "conditions", "conditions.json")
REGIONS_OUT = os.path.join(HERE, "..", "conditions", "regions")
REGIONS_FILE = os.path.join(HERE, "conditions_regions.json")

# The one box the job queried from 2026-10-05 to 2026-10-07 (the Smokies to the Potomac). App builds
# from those days read `fires.box` as the checked ground, so it is still queried, kept and published.
LEGACY_BOX = (-84.2, 35.3, -76.2, 40.0)
BOX = LEGACY_BOX  # the traffic feeds (Virginia only) still filter by it
FIRE_SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT")
FIRE_QUERIES_MAX = 6  # the fire boxes are merged into at most this many rectangles (per source)
FIRE_CAP = 300  # detections kept per region
AIRNOW_SLICES = 3  # each run asks AirNow about one slice of the regions
OPEN_METEO_BATCH = 40  # centers per Open-Meteo call
EBIRD_PAUSE = 0.15  # seconds between centers (two eBird calls each)


def load_regions(path=REGIONS_FILE):
    """The registry's regions: id, name, states, side, fireMiles, box, fireBox, centers, legacyBirds, mainFile."""
    with open(path) as f:
        return json.load(f)["regions"]


def centers_of(regions):
    """Every center with its region and side; air reads all of them, eBird skips the air-only ones."""
    out = []
    for r in regions:
        for c in r["centers"]:
            out.append({"id": c["id"], "region": r["id"], "side": c["side"], "name": c["name"],
                        "state": c.get("state", ""), "lat": c["lat"], "lng": c["lng"],
                        "air_only": bool(c.get("airOnly"))})
    return out


REGIONS = load_regions() if os.path.exists(REGIONS_FILE) else []
CENTERS = centers_of(REGIONS)


def get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout).read()


def safe(fn):
    """Run one source; on failure keep going, and say so without echoing URLs (they carry keys)."""
    try:
        return fn()
    except Exception as e:
        return {"status": f"error: {type(e).__name__}"}


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# Boxes are (west, south, east, north).
def box_area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def box_union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def box_contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def in_box(lat, lon, b):
    return b[1] <= lat <= b[3] and b[0] <= lon <= b[2]


def box_miles(lat, lon, b):
    """Miles from a point to the nearest edge of a box (0 inside it)."""
    dlat = max(b[1] - lat, 0.0, lat - b[3])
    dlon = max(b[0] - lon, 0.0, lon - b[2])
    return math.hypot(dlat * 69.0, dlon * 69.0 * math.cos(math.radians(lat)))


def merge_boxes(boxes, max_n):
    """Merge boxes pairwise, the cheapest union first, until at most max_n are left; the result covers every input."""
    boxes = [tuple(b) for b in boxes]
    while len(boxes) > max_n:
        best = None
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                u = box_union(boxes[i], boxes[j])
                cost = box_area(u) - box_area(boxes[i]) - box_area(boxes[j])
                if best is None or cost < best[0]:
                    best = (cost, i, j, u)
        _, i, j, u = best
        boxes = [b for k, b in enumerate(boxes) if k not in (i, j)] + [u]
    return boxes


def nearest_region(lat, lon, regions):
    """The region whose data box the point is in or nearest to."""
    best = None
    for r in regions:
        d = box_miles(lat, lon, r["box"])
        if best is None or d < best[0]:
            best = (d, r["id"])
    return best[1] if best else None


def firms_csv(key, source, box):
    area = ",".join(str(round(v, 3)) for v in box)
    return get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/2").decode()


def cap_fires(detections, regions, cap=FIRE_CAP):
    """Keep each region's list under `cap`: a big fire is hundreds of pixels, so first one per
    0.05-degree cell and day (the newest), then the nearest to the region's data box.
    Returns (kept, thinned region ids, truncated region ids)."""
    by_region = {}
    for d in detections:
        by_region.setdefault(d.get("region"), []).append(d)
    boxes = {r["id"]: r["box"] for r in regions}
    kept, thinned, truncated = [], [], []
    for rid, items in by_region.items():
        if len(items) > cap:
            cells = {}
            for d in items:
                k = (round(d["lat"] / 0.05), round(d["lon"] / 0.05), d["detected"][:10])
                if k not in cells or d["detected"] > cells[k]["detected"]:
                    cells[k] = d
            items = list(cells.values())
            thinned.append(rid)
        if len(items) > cap:
            box = boxes.get(rid, LEGACY_BOX)
            items.sort(key=lambda d: box_miles(d["lat"], d["lon"], box))
            items = items[:cap]
            truncated.append(rid)
        kept.extend(items)
    kept.sort(key=lambda d: d["detected"], reverse=True)
    return kept, sorted(x for x in thinned if x), sorted(x for x in truncated if x)


def fires(regions=None):
    regions = REGIONS if regions is None else regions
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    cover = [tuple(r["fireBox"]) for r in regions] + [LEGACY_BOX]
    queries = merge_boxes(cover, FIRE_QUERIES_MAX)
    out, seen, sources, answered = [], set(), {}, [False] * len(queries)
    for source in FIRE_SOURCES:
        notes = []
        for qi, q in enumerate(queries):
            try:
                text = firms_csv(key, source, q)
            except Exception as e:
                notes.append(f"error: {type(e).__name__}")
                continue
            if not text.startswith("latitude"):
                # FIRMS answers a bad key or a busy server with a short text message.
                notes.append("bad-response: " + text.strip()[:60].replace(key, "…"))
                continue
            answered[qi] = True
            notes.append(f"ok ({max(0, text.count(chr(10)) - 1)} rows)")
            for r in csv.DictReader(io.StringIO(text)):
                conf = (r.get("confidence") or "").strip().lower()
                if conf in ("l", "low") or (conf.isdigit() and int(conf) < 30):
                    continue
                lat, lon = round(float(r["latitude"]), 4), round(float(r["longitude"]), 4)
                if not any(in_box(lat, lon, b) for b in cover):
                    continue
                t = (r.get("acq_time") or "0000").zfill(4)
                when = f"{r['acq_date']}T{t[:2]}:{t[2:]}:00Z"
                k = (round(lat, 2), round(lon, 2), r["acq_date"])
                if k in seen:
                    continue
                seen.add(k)
                out.append({"lat": lat, "lon": lon, "detected": when, "source": source.split("_NRT")[0],
                            "confidence": conf, "frp": float(r["frp"]) if r.get("frp") else None,
                            "day": r.get("daynight") == "D", "region": nearest_region(lat, lon, regions)})
        sources[source] = "; ".join(notes)
    out.sort(key=lambda d: d["detected"], reverse=True)
    out, thinned, truncated = cap_fires(out, regions)
    # A box counts as checked only when some source answered the rectangle that holds it.
    checked = [list(b) for b in cover if any(ok and box_contains(q, b) for ok, q in zip(answered, queries))]
    status = "ok" if any(answered) else "error: no source answered"
    return {"status": status, "sources": sources, "detections": out, "box": list(LEGACY_BOX), "boxes": checked,
            "queried": [list(q) for q in queries], "thinned": thinned, "truncated": truncated}


AQI_BANDS = [(50, "Good", 1), (100, "Moderate", 2), (150, "Unhealthy for Sensitive Groups", 3),
             (200, "Unhealthy", 4), (300, "Very Unhealthy", 5), (10**6, "Hazardous", 6)]


def aqi_category(aqi):
    for top, name, level in AQI_BANDS:
        if aqi <= top:
            return name, level


def airnow_due(region_id, run=None):
    """Whether AirNow reads this region on this run: a third of the regions each run, by a stable
    hash of the id, so a key's hourly limit holds at 40 parks (8 runs a day, 3 slices)."""
    run = datetime.now(timezone.utc).hour // 3 if run is None else run
    # md5, not hash(): the same slice on every machine and run, and well mixed for short ids
    return int(hashlib.md5(region_id.encode()).hexdigest(), 16) % AIRNOW_SLICES == run % AIRNOW_SLICES


def airnow(centers):
    """EPA AirNow web services (key AIRNOW_API_KEY). Returns (obs, forecasts, covered center ids, note).
    A refusal of the key itself (HTTP 401, 403, 410) stops the run at once instead of repeating it per center."""
    key = os.environ.get("AIRNOW_API_KEY", "").strip()
    if not key:
        return [], [], set(), "no-key"
    obs, fc, areas, covered = [], [], set(), set()
    today = date.today()
    failures, why = 0, ""
    for c in centers:
        base = f"latitude={c['lat']}&longitude={c['lng']}&distance=50&format=application/json&API_KEY={key}"
        fbase = base.replace("distance=50", "distance=150")  # forecasts cover fewer, larger areas
        try:
            current = json.loads(get(f"https://www.airnowapi.org/aq/observation/latLong/current/?{base}"))
        except urllib.error.HTTPError as e:
            failures += 1
            why = f"HTTP {e.code}"
            if e.code in (401, 403, 410):
                return obs, fc, covered, f"key refused ({why}), stopped"
            continue
        except Exception as e:
            failures += 1
            why = type(e).__name__
            continue
        for o in current:
            covered.add(c["id"])
            k = (o["ReportingArea"], o["ParameterName"])
            if k in areas:
                continue
            areas.add(k)
            obs.append({"area": o["ReportingArea"], "state": o["StateCode"], "lat": o["Latitude"], "lon": o["Longitude"],
                        "pollutant": o["ParameterName"], "aqi": o["AQI"], "category": o["Category"]["Name"],
                        "level": o["Category"]["Number"], "observed": f"{o['DateObserved'].strip()} {o['HourObserved']}:00 {o['LocalTimeZone']}",
                        "near": c["id"], "side": c["side"], "region": c["region"], "source": "airnow"})
        for d in (today, today + timedelta(days=1)):
            try:
                forecasts = json.loads(get(f"https://www.airnowapi.org/aq/forecast/latLong/?{fbase}&date={d.isoformat()}"))
            except Exception:
                forecasts = []
            for f in forecasts:
                fc.append({"area": f["ReportingArea"], "date": f["DateForecast"].strip(), "pollutant": f["ParameterName"],
                           "aqi": f["AQI"], "category": f["Category"]["Name"], "level": f["Category"]["Number"],
                           "actionDay": bool(f.get("ActionDay")), "discussion": (f.get("Discussion") or "")[:600],
                           "near": c["id"], "side": c["side"], "region": c["region"], "source": "airnow"})
    return obs, fc, covered, (f"{failures} requests failed ({why})" if failures else "ok")


OM_POLLUTANTS = (("us_aqi_pm2_5", "PM2.5"), ("us_aqi_ozone", "O3"), ("us_aqi_pm10", "PM10"))


def open_meteo_batch(centers):
    import urllib.parse
    q = urllib.parse.urlencode({
        "latitude": ",".join(str(c["lat"]) for c in centers), "longitude": ",".join(str(c["lng"]) for c in centers),
        "current": "us_aqi," + ",".join(k for k, _ in OM_POLLUTANTS),
        "hourly": "us_aqi," + ",".join(k for k, _ in OM_POLLUTANTS),
        "forecast_days": 2, "timezone": "auto"})
    data = json.loads(get(f"https://air-quality-api.open-meteo.com/v1/air-quality?{q}"))
    if isinstance(data, dict):
        data = [data]
    return data


def open_meteo(centers):
    """Modeled US AQI (Open-Meteo, from Copernicus CAMS; free, no key) for centers AirNow did not answer,
    OPEN_METEO_BATCH centers per call. One reading per center (area = the center's name) and its worst
    pollutant, plus the daily worst for today and tomorrow. These are model estimates, not monitor
    readings, and are marked source open-meteo."""
    if not centers:
        return [], []
    obs, fc = [], []
    for start in range(0, len(centers), OPEN_METEO_BATCH):
        batch = centers[start:start + OPEN_METEO_BATCH]
        data = open_meteo_batch(batch)
        for c, r in zip(batch, data):
            cur = r.get("current") or {}
            if cur.get("us_aqi") is None:
                continue
            subs = [(cur.get(k), name) for k, name in OM_POLLUTANTS if cur.get(k) is not None]
            aqi = int(round(cur["us_aqi"]))
            pol = max(subs)[1] if subs else "PM2.5"
            cat, lvl = aqi_category(aqi)
            day, hour = cur["time"].split("T")
            tag = {"area": c["name"], "state": c["state"], "near": c["id"], "side": c["side"], "region": c["region"], "source": "open-meteo"}
            obs.append({**tag, "lat": c["lat"], "lon": c["lng"], "pollutant": pol, "aqi": aqi, "category": cat, "level": lvl,
                        "observed": f"{day} {int(hour[:2])}:00 local"})
            h = r.get("hourly") or {}
            for d in sorted({t[:10] for t in h.get("time", [])}):
                idx = [i for i, t in enumerate(h["time"]) if t.startswith(d) and h["us_aqi"][i] is not None]
                if not idx:
                    continue
                top = max(idx, key=lambda i: h["us_aqi"][i])
                faqi = int(round(h["us_aqi"][top]))
                fsubs = [(h[k][top], name) for k, name in OM_POLLUTANTS if h.get(k) and h[k][top] is not None]
                fcat, flvl = aqi_category(faqi)
                fc.append({**tag, "date": d, "pollutant": max(fsubs)[1] if fsubs else pol, "aqi": faqi, "category": fcat,
                           "level": flvl, "actionDay": False, "discussion": ""})
    return obs, fc


def air(centers=None, run=None):
    """AirNow first (official monitors and state forecasts) for this run's slice of the regions;
    Open-Meteo's modeled AQI for every center AirNow did not answer. AirNow's key was refused
    with HTTP 410 on 2026-10-05, which is why the fallback exists."""
    centers = CENTERS if centers is None else centers
    due = [c for c in centers if airnow_due(c["region"], run)]
    obs, fc, covered, note = airnow(due)
    sources = {"airnow": f"{note} ({len(obs)} readings; {len(due)} of {len(centers)} centers due this run)"}
    missing = [c for c in centers if c["id"] not in covered]
    try:
        o2, f2 = open_meteo(missing)
        obs += o2
        fc += f2
        sources["open-meteo"] = f"ok ({len(o2)} of {len(missing)} centers)"
    except Exception as e:
        sources["open-meteo"] = f"error: {type(e).__name__}"
    uniq = {}
    for f in fc:
        uniq[(f["area"], f["date"], f["pollutant"])] = f
    if not obs:
        return {"status": "error: no air source answered", "sources": sources, "observations": [], "forecasts": []}
    return {"status": "ok", "sources": sources, "observations": obs, "forecasts": list(uniq.values())}


def birds(centers=None, regions=None):
    centers = CENTERS if centers is None else centers
    regions = REGIONS if regions is None else regions
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    h = {"X-eBirdApiToken": key}
    # Shenandoah's and the forest's lists keep the old park/forest keys; any failure there makes the whole
    # source stale, as before. Every other region's list is its own, and a failure loses only that region.
    legacy = {r["id"]: r.get("legacyBirds") for r in regions if r.get("legacyBirds")}
    sides = {"park": {}, "forest": {}}
    by_region, failed = {}, set()
    for c in centers:
        if c.get("air_only"):
            continue
        side = legacy.get(c["region"])
        q = f"lat={c['lat']}&lng={c['lng']}&dist=25&back=7"
        try:
            recent = json.loads(get(f"https://api.ebird.org/v2/data/obs/geo/recent?{q}&maxResults=400", h))
            notable = json.loads(get(f"https://api.ebird.org/v2/data/obs/geo/recent/notable?{q}&detail=simple", h))
        except Exception:
            if side:
                raise
            failed.add(c["region"])
            continue
        if EBIRD_PAUSE:
            time.sleep(EBIRD_PAUSE)
        rare = {o["speciesCode"] for o in notable}
        for o in recent + notable:
            if o.get("locationPrivate"):
                continue
            s = sides[side] if side else by_region.setdefault(c["region"], {})
            prev = s.get(o["speciesCode"])
            item = {"code": o["speciesCode"], "name": o["comName"], "sci": o["sciName"], "count": o.get("howMany"),
                    "seen": o["obsDt"], "place": o["locName"], "lat": round(o["lat"], 3), "lon": round(o["lng"], 3),
                    "notable": o["speciesCode"] in rare, "near": c["name"]}
            if prev is None or item["seen"] > prev["seen"]:
                s[o["speciesCode"]] = {**item, "notable": item["notable"] or (prev or {}).get("notable", False)}
    order = lambda v: sorted(v.values(), key=lambda x: (not x["notable"], x["name"]))
    out = {"status": "ok", **{k: order(v) for k, v in sides.items()},
           "byRegion": {r: order(v) for r, v in by_region.items()}}
    if failed:
        out["failedRegions"] = sorted(failed)  # assemble() fills these from the last good copies
    return out


# VDOT road-weather stations on the way up to the park and the forest.
PASSES = {
    "NWRO-ESS-US211-E-00043": ("Thornton Gap (US 211)", "park"),
    "NWRO-ESS-US33-W-00455": ("Swift Run Gap (US 33)", "park"),
    "NWRO-ESS-I64-W-01011": ("Afton Mountain (I-64, Rockfish Gap)", "park"),
    "NWRO-ESS-I66-W-00149": ("Front Royal approach (I-66)", "park"),
    "NWRO-ESS-US211-E-00042": ("New Market Gap (US 211)", "forest"),
    "NWRO-ESS-SR259-Bergton": ("Bergton (VA 259)", "forest"),
    "NWRO-ESS-I64-E-00450": ("North Mountain (I-64)", "forest"),
    "NWRO-ESS-I81-N-01830": ("Natural Bridge (I-81)", "forest"),
}


def num(pattern, text, missing_at=1000):
    """A number from VDOT's description text. Missing sensors read 1001 (or
    1000001 for visibility, whose real range tops out at 2000 m); callers
    also treat an exact 0.0 temperature or visibility as missing."""
    import re
    m = re.search(pattern + r":?\s*(-?[\d.]+)", text)
    if not m:
        return None
    v = float(m.group(1))
    return None if v >= missing_at else v


def roads():
    token = os.environ.get("VDOT_TOKEN", "").strip()
    if not token:
        return {"status": "no-key"}
    import re, urllib.parse
    import xml.etree.ElementTree as ET
    url = ("https://data.511-atis-ttrip-prod.iteriscloud.com/smarterRoads/weather/rwisGEORSS/current/rwis_georss.xml?token="
           + urllib.parse.quote(token, safe=""))
    root = ET.fromstring(get(url))
    out = []
    for it in root.iter("item"):
        sid = (it.findtext("title") or "").strip()
        if sid not in PASSES:
            continue
        d = it.findtext("description") or ""
        lat, lon = (float(v) for v in (it.findtext("{http://www.georss.org/georss}point") or "0 0").split())
        air, surf = num("Air Temperature", d), num("Surface Temperature", d)
        vis = num("Visibility", d, missing_at=1000000)
        up = re.search(r"Updated At:\s*(\S+)", d)
        name, side = PASSES[sid]
        out.append({"id": sid, "name": name, "side": side, "lat": lat, "lon": lon,
                    "airC": None if air in (None, 0.0) else air,
                    "surfaceC": None if surf in (None, 0.0) else surf,
                    "visibilityM": None if vis in (None, 0.0) else vis,
                    "precipRate": num("Precipitation Rate", d),
                    "humidity": num("Relative Humidity", d),
                    "windKph": num("Average Wind Speed", d),
                    "updated": up.group(1) if up else None})
    return {"status": "ok" if out else "error: no stations", "stations": out}


VDOT_BASE = "https://data.511-atis-ttrip-prod.iteriscloud.com/smarterRoads"
# Each SmarterRoads dataset has its own token; each feed comes filtered and unfiltered.
VDOT_FEEDS = {
    "incidents": ("VDOT_INCIDENTS_TOKEN", ["/incidentFiltered/incidentFilteredGEORSS/current/incidentFiltered_georss.xml",
                                           "/incidentUnfiltered/incidentUnfilteredGEORSS/current/incidentUnfiltered_georss.xml"]),
    "events": ("VDOT_EVENTS_TOKEN", ["/eventFiltered/eventFilteredGEORSS/current/eventFiltered_georss.xml",
                                     "/eventUnfiltered/eventUnfilteredGEORSS/current/eventUnfiltered_georss.xml"]),
    "roadConditions": ("VDOT_ROAD_CONDITION_TOKEN", ["/roadCondition/weatherLongGeorss/current/weather_long_georss.xml"]),
    "weatherShort": ("VDOT_WEATHER_SHORT_TOKEN", ["/incidentUnfiltered/weatherShortGeorss/current/weather_short_georss.xml"]),
    "weatherAll": ("VDOT_WEATHER_LONG_TOKEN", ["/roadCondition/weatherAllGeorss/current/weather_all_georss.xml"]),
}


ANCHORS = (read_json(os.path.join(HERE, "..", "conditions", "anchors.json"))
           or read_json(os.path.join(HERE, "anchors.json")) or {"points": []})["points"]
NEAR_MILES = 3.0


def near_anchor(lat, lon):
    """(miles, side) to the nearest trailhead/parking anchor, or None beyond NEAR_MILES."""
    import math
    best = None
    for x, y, side in ANCHORS:
        d = math.hypot((x - lon) * 54.6, (y - lat) * 69.0)
        if best is None or d < best[0]:
            best = (d, side)
    return best if best and best[0] <= NEAR_MILES else None


def vdot_items(xml_bytes):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        pt, line = None, None
        for child in it:
            tag = child.tag.split("}")[-1]
            if tag == "point" and child.text:
                pt = [float(v) for v in child.text.split()[:2]]
            elif tag == "line" and child.text:
                vals = [float(v) for v in child.text.split()]
                line = [vals[i:i + 2] for i in range(0, len(vals) - 1, 2)]
        if pt is None and line:
            pt = line[0]
        if pt is None:
            continue
        lat, lon = pt
        if not (BOX[1] <= lat <= BOX[3] and BOX[0] <= lon <= BOX[2]):
            continue
        # Keep only what's near a trailhead or park/Parkway parking (any vertex of a closure line).
        hits = [h for h in (near_anchor(la, lo) for la, lo in ([pt] + (line or []))) if h]
        if not hits:
            continue
        miles, side = min(hits)
        out.append({"title": (it.findtext("title") or "").strip(), "description": (it.findtext("description") or "").strip()[:800],
                     "lat": round(lat, 5), "lon": round(lon, 5), "line": line[:60] if line else None,
                     "published": (it.findtext("pubDate") or "").strip() or None,
                     "side": side, "milesFromTrailhead": round(miles, 1),
                     "id": (it.findtext("guid") or "").strip() or None, "link": (it.findtext("link") or "").strip() or None,
                     "tags": sorted({c.tag.split("}")[-1] for c in it})})
    return out


def event_window(text):
    """(start, end) from VDOT's "from 09/25/26 at 9:00 AM until 09/25/26 at 3:30 PM", Eastern local time."""
    import re
    m = re.search(r"from (\d{2}/\d{2}/\d{2}) at (\d{1,2}:\d{2} [AP]M) until (\d{2}/\d{2}/\d{2}) at (\d{1,2}:\d{2} [AP]M)", text)
    if not m:
        return None, None
    f = "%m/%d/%y %I:%M %p"
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", f), datetime.strptime(f"{m.group(3)} {m.group(4)}", f)


def vdot():
    import urllib.parse
    res = {}
    for name, (secret, paths) in VDOT_FEEDS.items():
        token = os.environ.get(secret, "").strip()
        if not token:
            res[name] = {"status": "no-key"}
            continue
        tried = {}
        for path in paths:
            try:
                data = get(f"{VDOT_BASE}{path}?token={urllib.parse.quote(token, safe='')}")
                items = vdot_items(data)
                if name == "events":
                    # Only what's on now or within 3 days (VDOT's times are Eastern; the runner is UTC).
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
                    keep = []
                    for it in items:
                        start, end = event_window(it["description"])
                        if start and end and end >= now and start <= now + timedelta(days=3):
                            it["start"], it["end"] = start.isoformat(), end.isoformat()
                            it["allLanesClosed"] = "All north lanes are closed" in it["description"] and "south lanes are closed" in it["description"] \
                                or "All east lanes are closed" in it["description"] and "west lanes are closed" in it["description"] \
                                or "road is closed" in it["description"].lower()
                            keep.append(it)
                    items = keep
                res[name] = {"status": "ok", "feed": path.split("/")[1], "items": items}
                break
            except Exception as e:
                tried[path.split("/")[1]] = type(e).__name__
        else:
            res[name] = {"status": "error", "tried": tried}
    return res


def pollen():
    """Tree, grass and weed pollen indexes (0 none … 5 very high) for today and
    the next days from Tomorrow.io (free plan; key TOMORROW_API_KEY)."""
    key = os.environ.get("TOMORROW_API_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    out = []
    for c in (CENTERS[1], CENTERS[4]):  # the park's middle and North River
        body = json.dumps({"location": f"{c['lat']},{c['lng']}", "fields": ["treeIndex", "grassIndex", "weedIndex"],
                           "timesteps": ["1d"], "units": "imperial", "timezone": "America/New_York",
                           "startTime": "now", "endTime": "nowPlus4d"}).encode()
        req = urllib.request.Request(f"https://api.tomorrow.io/v4/timelines?apikey={key}", data=body,
                                     headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            # Tomorrow.io explains refusals in the body (e.g. a field not on the plan); keep it, not the URL.
            return {"status": f"error: HTTP {e.code}", "detail": e.read()[:300].decode("utf-8", "ignore")}
        days = []
        for t in data["data"]["timelines"][0]["intervals"]:
            v = t.get("values", {})
            days.append({"date": t["startTime"][:10], "tree": v.get("treeIndex"), "grass": v.get("grassIndex"), "weed": v.get("weedIndex")})
        out.append({"near": c["id"], "side": c["side"], "days": days})
    return {"status": "ok", "places": out}


def stale_parts(new, old, keys):
    """Where a part failed this run but the last file had it, keep the old one marked stale."""
    old = old or {}
    for k in keys:
        if str((new.get(k) or {}).get("status", "")).startswith("error") and (old.get(k) or {}).get("status") in ("ok", "stale"):
            new[k] = {**old[k], "status": "stale", "staleSince": new["fetchedAt"]}
    return new


def region_file(now, region, air_out, birds_out):
    """One region's file: its air readings and forecasts and its bird list."""
    rid = region["id"]
    obs = [o for o in (air_out.get("observations") or []) if o.get("region") == rid]
    fc = [f for f in (air_out.get("forecasts") or []) if f.get("region") == rid]
    a_status = str(air_out.get("status", "error"))
    if a_status == "ok" and not obs:
        a_status = "error: no reading for the region"  # the sources answered, but not for these centers
    side = region.get("legacyBirds")
    b_list = birds_out.get(side) if side else (birds_out.get("byRegion") or {}).get(rid)
    b_status = str(birds_out.get("status", "error"))
    if b_status == "ok" and b_list is None:
        b_status = "error: no list for the region"
    return {"fetchedAt": now, "region": rid,
            "air": {"status": a_status, "observations": obs, "forecasts": fc},
            "birds": {"status": b_status, "list": b_list or []}}


def assemble(now, parts, regions, old_main=None, old_region=None):
    """The main file and every region's file from one run's parts (fires, air, birds, roads, traffic).
    `old_main` is the last main file; `old_region(id)` the last file of a region (for stale fallbacks)."""
    old_main = old_main or {}
    old_region = old_region or (lambda rid: None)
    main_ids = {r["id"] for r in regions if r.get("mainFile")}
    air_out = parts["air"] if isinstance(parts.get("air"), dict) else {"status": "error"}
    birds_out = dict(parts["birds"]) if isinstance(parts.get("birds"), dict) else {"status": "error"}
    failed_birds = birds_out.pop("failedRegions", [])
    files = {}
    for r in regions:
        files[r["id"]] = stale_parts(region_file(now, r, air_out, birds_out), old_region(r["id"]), ("air", "birds"))
    main_air = {**air_out, "observations": [o for o in (air_out.get("observations") or []) if o.get("region") in main_ids],
                "forecasts": [f for f in (air_out.get("forecasts") or []) if f.get("region") in main_ids]}
    by_region = {k: v for k, v in (birds_out.get("byRegion") or {}).items() if k in main_ids}
    # Some main-file regions' eBird calls failed: keep their last list rather than publish none.
    for rid in failed_birds:
        if rid in main_ids:
            prev = ((old_main.get("birds") or {}).get("byRegion") or {}).get(rid)
            if prev:
                by_region[rid] = prev
    main_birds = {**birds_out, "byRegion": by_region} if birds_out.get("status") == "ok" else birds_out
    main = {"fetchedAt": now, "fires": parts["fires"], "air": main_air, "birds": main_birds, "roads": parts["roads"],
            "traffic": parts.get("traffic"), "regionFiles": sorted(r["id"] for r in regions if r["id"] not in main_ids)}
    return stale_parts(main, old_main, ("fires", "air", "birds", "roads")), files


def main():
    if not REGIONS:
        print("no regions: conditions_regions.json is missing beside this script", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = {"fires": safe(fires), "air": safe(air), "birds": safe(birds), "roads": safe(roads)}
    for k in ("fires", "air", "birds", "roads"):
        print("fetched", k, str(parts[k].get("status"))[:200])
    parts["traffic"] = safe(vdot)
    # Pollen: Tomorrow.io's free plan refuses the pollen fields (HTTP 403 "fields are not
    # allowed", 2026-09-23), so pollen() stays unused until there's a plan that includes them.
    out, files = assemble(now, parts, REGIONS, read_json(OUT),
                          lambda rid: read_json(os.path.join(REGIONS_OUT, f"{rid}.json")))
    os.makedirs(REGIONS_OUT, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    for rid, data in files.items():
        with open(os.path.join(REGIONS_OUT, f"{rid}.json"), "w") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
    summary = {k: (v.get("status"), {kk: len(vv) for kk, vv in v.items() if isinstance(vv, list)}) for k, v in out.items() if isinstance(v, dict)}
    print(summary)
    print("fire sources:", out["fires"].get("sources"), "boxes:", len(out["fires"].get("boxes") or []),
          "thinned:", out["fires"].get("thinned"), "truncated:", out["fires"].get("truncated"))
    print("region files:", {rid: (d["air"]["status"], len(d["air"]["observations"]), d["birds"]["status"], len(d["birds"]["list"]))
                            for rid, d in files.items()})
    for k, v in (out.get("traffic") or {}).items():
        if isinstance(v, dict):
            items = v.get("items") or []
            print("traffic", k, v.get("status"), v.get("feed"), v.get("tried"), len(items), "in area")
            for it in items[:2]:
                print("   ", it["tags"], "|", it["title"][:80], "|", it["description"][:300])


if __name__ == "__main__":
    sys.exit(main())
