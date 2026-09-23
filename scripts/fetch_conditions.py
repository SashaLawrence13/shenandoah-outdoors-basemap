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
    out, seen = [], set()
    for source in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"):
        try:
            text = get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/2").decode()
        except Exception:
            continue
        if not text.startswith("latitude"):
            continue
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
    return {"status": "ok", "detections": out}


def air():
    key = os.environ.get("AIRNOW_API_KEY", "").strip()
    if not key:
        return {"status": "no-key"}
    obs, fc, areas = [], [], set()
    today = date.today()
    for c in CENTERS:
        base = f"latitude={c['lat']}&longitude={c['lng']}&distance=50&format=application/json&API_KEY={key}"
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
            for f in json.loads(get(f"https://www.airnowapi.org/aq/forecast/latLong/?{base}&date={d.isoformat()}")):
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


def main():
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fires": safe(fires), "air": safe(air), "birds": safe(birds)}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    summary = {k: (v.get("status"), {kk: len(vv) for kk, vv in v.items() if isinstance(vv, list)}) for k, v in out.items() if isinstance(v, dict)}
    print(summary)


if __name__ == "__main__":
    sys.exit(main())
