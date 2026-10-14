"""fetch_nps_bulk.py: raw NPS Data API pulls for the app's park data pipeline.

Writes park/nps/<parkCode>/<endpoint>.json ({"fetchedAt", "parkCode", "endpoint",
"total", "data": [...]}) for each (park, endpoint) pair below, with the records
exactly as the API returns them. The app repo's process_nps_region.py reads
them (copied into data-pipeline/raw/nps/<day>/<id>/api/). A pair is refetched
only when its file is older than REFRESH_DAYS. Key: NPS_API_KEY.
Added 2026-10-14 for the Quality lane (the shared DEMO_KEY was refused).
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "park", "nps")
BASE = "https://developer.nps.gov/api/v1/"
UA = "Mossback/1.0 (hiking app; github.com/SashaLawrence13)"
REFRESH_DAYS = 7
PAUSE = 0.4
PAGE = 200

SECOND_SOURCE_PARKS = ["arch", "badl", "bibe", "cany", "care", "crla", "ever", "glac", "grca", "grte",
                       "meve", "mora", "olym", "romo", "sagu", "yell", "yose", "indu", "zion"]
PAIRS = (
    [("seki", "places"), ("redw", "places"), ("redw", "thingstodo"), ("redw", "campgrounds"),
     ("redw", "visitorcenters"), ("redw", "alerts"), ("redw", "parks")]
    + [(p, e) for p in SECOND_SOURCE_PARKS for e in ("places", "thingstodo")]
)


def get_json(endpoint, park, start, key):
    q = urllib.parse.urlencode({"parkCode": park, "limit": PAGE, "start": start})
    req = urllib.request.Request(f"{BASE}{endpoint}?{q}", headers={"User-Agent": UA, "X-Api-Key": key})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def fresh(path):
    try:
        with open(path) as f:
            at = json.load(f).get("fetchedAt", "")
        age = datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))
        return age.days < REFRESH_DAYS
    except (OSError, ValueError, AttributeError):
        return False


def fetch_all(endpoint, park, key):
    data, start, total = [], 0, None
    while True:
        page = get_json(endpoint, park, start, key)
        rows = page.get("data", [])
        total = int(page.get("total", len(rows)) or 0)
        data.extend(rows)
        start += PAGE
        if not rows or start >= total:
            return data, total
        time.sleep(PAUSE)


def main():
    key = os.environ.get("NPS_API_KEY", "").strip()
    if not key:
        print("NPS_API_KEY not set; skipping NPS bulk pulls")
        return 0
    done = failed = 0
    for park, endpoint in PAIRS:
        path = os.path.join(OUT_DIR, park, f"{endpoint}.json")
        if fresh(path):
            continue
        try:
            data, total = fetch_all(endpoint, park, key)
        except Exception as e:  # keep the last file; never echo URLs
            print(f"{park}/{endpoint}: error {type(e).__name__}")
            failed += 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(path, "w") as f:
            json.dump({"fetchedAt": now, "parkCode": park, "endpoint": endpoint, "total": total, "data": data},
                      f, indent=1, ensure_ascii=False)
        done += 1
        time.sleep(PAUSE)
    print(f"nps bulk: {done} files written, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
