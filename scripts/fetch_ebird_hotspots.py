"""fetch_ebird_hotspots.py: eBird birding hotspots per Mossback region.

Writes conditions/hotspots/<region>.json ({"fetchedAt", "region", "hotspots": [
{"locId", "name", "lat", "lng", "numSpeciesAllTime"}]}) for every region in
scripts/conditions_regions.json, so the app's Birding chip can list the best
places to bird near a hike. Hotspots change slowly, so a region is refetched
only when its file is older than REFRESH_DAYS. Key: EBIRD_API_KEY (the same
secret fetch_conditions.py uses). Added 2026-10-14 for the Features lane.
"""

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS_FILE = os.path.join(HERE, "conditions_regions.json")
OUT_DIR = os.path.join(HERE, "..", "conditions", "hotspots")
UA = "Mossback/1.0 (hiking app; github.com/SashaLawrence13)"
REFRESH_DAYS = 7
PAUSE = 0.5  # seconds between regions
MAX_DIST_KM = 500  # eBird's limit for the hotspot geo search


def get(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    return urllib.request.urlopen(req, timeout=60).read()


def km_between(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fresh(path):
    try:
        with open(path) as f:
            at = json.load(f).get("fetchedAt", "")
        age = datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))
        return age.days < REFRESH_DAYS
    except (OSError, ValueError, AttributeError):
        return False


def hotspots_for(region, headers):
    west, south, east, north = region["box"]
    lat, lng = (south + north) / 2, (west + east) / 2
    dist = min(MAX_DIST_KM, math.ceil(km_between(lat, lng, north, east)) + 1)
    url = f"https://api.ebird.org/v2/ref/hotspot/geo?lat={lat:.4f}&lng={lng:.4f}&dist={dist}&fmt=json"
    rows = json.loads(get(url, headers))
    out = []
    for h in rows:
        if not (west <= h["lng"] <= east and south <= h["lat"] <= north):
            continue
        out.append({"locId": h["locId"], "name": h["locName"], "lat": round(h["lat"], 5),
                    "lng": round(h["lng"], 5), "numSpeciesAllTime": h.get("numSpeciesAllTime", 0)})
    out.sort(key=lambda h: -h["numSpeciesAllTime"])
    return out


def main():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if not key:
        print("EBIRD_API_KEY not set; skipping hotspots")
        return 0
    headers = {"X-eBirdApiToken": key}
    with open(REGIONS_FILE) as f:
        regions = json.load(f)["regions"]
    os.makedirs(OUT_DIR, exist_ok=True)
    done = failed = 0
    for r in regions:
        path = os.path.join(OUT_DIR, f"{r['id']}.json")
        if fresh(path):
            continue
        try:
            spots = hotspots_for(r, headers)
        except Exception as e:  # keep the last file; never echo URLs (they don't carry the key, but stay quiet)
            print(f"{r['id']}: error {type(e).__name__}")
            failed += 1
            continue
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(path, "w") as f:
            json.dump({"fetchedAt": now, "region": r["id"], "hotspots": spots}, f, indent=1, ensure_ascii=False)
        done += 1
        time.sleep(PAUSE)
    print(f"hotspots: {done} regions written, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
