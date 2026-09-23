#!/usr/bin/env python3
"""Live conditions for the Mossback app, published as conditions/conditions.json
on this repo's Pages (the keys stay in this repo's Actions secrets):

- fires: NASA FIRMS active-fire detections (VIIRS on Suomi NPP, NOAA-20 and
  NOAA-21, and MODIS) in the park and forest box, last 2 days. Key: FIRMS_MAP_KEY.
  Public domain (NASA). Low-confidence detections are dropped.
- air: EPA AirNow current observations and today's/tomorrow's forecasts
  for reporting areas near the park and the forest's districts. Key:
  AIRNOW_API_KEY. AirNow data is preliminary; attribution to AirNow.
- birds: eBird recent (last 7 days) and notable sightings around the park
  and the forest's districts. Key: EBIRD_API_KEY. Observations at private
  locations are left out; eBird already hides sensitive species.
- roads: VDOT SmarterRoads road-weather stations (RWIS) on the passes up to
  the park and the forest: air and pavement temperature, visibility (fog),
  precipitation. Key: VDOT_TOKEN (the owner's SmarterRoads token).
Run by .github/workflows/alerts.yml with the alerts.
"""
import csv, io, json, os, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

UA = "Mossback conditions (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
OUT = os.path.join(os.path.dirname(__file__), "..", "conditions", "conditions.json")
BOX = (-80.2, 37.3, -77.8, 39.2)  # west, south, east, north: the park, the three districts, the Parkway

# Where people are, for air and birds.
CENTERS = [
    {"id": "park-north", "side": "park", "name": "Shenandoah NP north", "lat": 38.80, "lng": -78.28},
    {"id": "park-central", "side": "park", "name": "Shenandoah NP central", "lat": 38.53, "lng": -78.44},
    {"id": "park-south", "side": "park", "name": "Shenandoah NP south", "lat": 38.20, "lng": -78.75},
    {"id": "lee", "side": "forest", "name": "Lee district", "lat": 38.88, "lng": -78.50},
    {"id": "north-river", "side": "forest", "name": "North River district", "lat": 38.40, "lng": -79.12},
    {"id": "glenwood-pedlar", "side": "forest", "name": "Glenwood-Pedlar district", "lat": 37.75, "lng": -79.25},
]


def get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout).read()


def safe(fn):
    """Run one source; on failure keep going, and say so without echoing URLs (they carry keys)."""
    try:
        return fn()
    except Exception as e:
        return {"status": f"error: {type(e).__name__}"}


def fires():
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    area = ",".join(str(v) for v in BOX)
    out, seen, sources = [], set(), {}
    for source in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"):
        try:
            text = get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/2").decode()
        except Exception as e:
            sources[source] = f"error: {type(e).__name__}"
            continue
        if not text.startswith("latitude"):
            # FIRMS answers a bad key or a busy server with a short text message.
            sources[source] = "bad-response: " + text.strip()[:60].replace(key, "…")
            continue
        sources[source] = f"ok ({max(0, text.count(chr(10)) - 1)} rows)"
        for r in csv.DictReader(io.StringIO(text)):
            conf = (r.get("confidence") or "").strip().lower()
            if conf in ("l", "low") or (conf.isdigit() and int(conf) < 30):
                continue
            lat, lon = round(float(r["latitude"]), 4), round(float(r["longitude"]), 4)
            t = (r.get("acq_time") or "0000").zfill(4)
            when = f"{r['acq_date']}T{t[:2]}:{t[2:]}:00Z"
            k = (round(lat, 2), round(lon, 2), r["acq_date"])
            if k in seen:
                continue
            seen.add(k)
            out.append({"lat": lat, "lon": lon, "detected": when, "source": source.split("_NRT")[0],
                        "confidence": conf, "frp": float(r["frp"]) if r.get("frp") else None,
                        "day": r.get("daynight") == "D"})
    out.sort(key=lambda d: d["detected"], reverse=True)
    good = sum(1 for v in sources.values() if v.startswith("ok"))
    return {"status": "ok" if good else "error: no source answered", "sources": sources, "detections": out}


def air():
    key = os.environ.get("AIRNOW_API_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    obs, fc, areas = [], [], set()
    today = date.today()
    for c in CENTERS:
        base = f"latitude={c['lat']}&longitude={c['lng']}&distance=50&format=application/json&API_KEY={key}"
        fbase = base.replace("distance=50", "distance=150")  # forecasts cover fewer, larger areas
        for o in json.loads(get(f"https://www.airnowapi.org/aq/observation/latLong/current/?{base}")):
            k = (o["ReportingArea"], o["ParameterName"])
            if k in areas:
                continue
            areas.add(k)
            obs.append({"area": o["ReportingArea"], "state": o["StateCode"], "lat": o["Latitude"], "lon": o["Longitude"],
                        "pollutant": o["ParameterName"], "aqi": o["AQI"], "category": o["Category"]["Name"],
                        "level": o["Category"]["Number"], "observed": f"{o['DateObserved'].strip()} {o['HourObserved']}:00 {o['LocalTimeZone']}",
                        "near": c["id"], "side": c["side"]})
        for d in (today, today + timedelta(days=1)):
            for f in json.loads(get(f"https://www.airnowapi.org/aq/forecast/latLong/?{fbase}&date={d.isoformat()}")):
                fc.append({"area": f["ReportingArea"], "date": f["DateForecast"].strip(), "pollutant": f["ParameterName"],
                           "aqi": f["AQI"], "category": f["Category"]["Name"], "level": f["Category"]["Number"],
                           "actionDay": bool(f.get("ActionDay")), "discussion": (f.get("Discussion") or "")[:600],
                           "near": c["id"], "side": c["side"]})
    uniq = {}
    for f in fc:
        uniq[(f["area"], f["date"], f["pollutant"])] = f
    return {"status": "ok", "observations": obs, "forecasts": list(uniq.values())}


def birds():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    h = {"X-eBirdApiToken": key}
    sides = {"park": {}, "forest": {}}
    for c in CENTERS:
        q = f"lat={c['lat']}&lng={c['lng']}&dist=25&back=7"
        recent = json.loads(get(f"https://api.ebird.org/v2/data/obs/geo/recent?{q}&maxResults=400", h))
        notable = json.loads(get(f"https://api.ebird.org/v2/data/obs/geo/recent/notable?{q}&detail=simple", h))
        rare = {o["speciesCode"] for o in notable}
        for o in recent + notable:
            if o.get("locationPrivate"):
                continue
            s = sides[c["side"]]
            prev = s.get(o["speciesCode"])
            item = {"code": o["speciesCode"], "name": o["comName"], "sci": o["sciName"], "count": o.get("howMany"),
                    "seen": o["obsDt"], "place": o["locName"], "lat": round(o["lat"], 3), "lon": round(o["lng"], 3),
                    "notable": o["speciesCode"] in rare, "near": c["name"]}
            if prev is None or item["seen"] > prev["seen"]:
                s[o["speciesCode"]] = {**item, "notable": item["notable"] or (prev or {}).get("notable", False)}
    return {"status": "ok", **{k: sorted(v.values(), key=lambda x: (not x["notable"], x["name"])) for k, v in sides.items()}}


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


ANCHORS = json.load(open(os.path.join(os.path.dirname(__file__), "..", "conditions", "anchors.json")))["points"]
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


def main():
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fires": safe(fires), "air": safe(air), "birds": safe(birds), "roads": safe(roads)}
    out["traffic"] = safe(vdot)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    summary = {k: (v.get("status"), {kk: len(vv) for kk, vv in v.items() if isinstance(vv, list)}) for k, v in out.items() if isinstance(v, dict)}
    print(summary)
    print("fire sources:", out["fires"].get("sources"))
    for k, v in (out.get("traffic") or {}).items():
        if isinstance(v, dict):
            items = v.get("items") or []
            print("traffic", k, v.get("status"), v.get("feed"), v.get("tried"), len(items), "in area")
            for it in items[:2]:
                print("   ", it["tags"], "|", it["title"][:80], "|", it["description"][:300])


if __name__ == "__main__":
    sys.exit(main())
