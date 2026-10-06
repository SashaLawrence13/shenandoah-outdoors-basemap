#!/usr/bin/env python3
"""The national forest's campgrounds (and other Recreation.gov facilities)
around the app's three George Washington NF districts (`facilities`) and, since
2026-10-05, the Potomac, Monongahela, Smokies and New River areas (`byRegion`,
with `coverage` listing the regions the query covers), from Recreation.gov's
RIDB API (key in the RIDB_API_KEY secret), published as ridb/ridb.json.
Facility details only: RIDB has no live availability.
Run by .github/workflows/alerts.yml.
"""
import html, json, os, re, sys, urllib.request
from datetime import datetime, timezone

UA = "Mossback (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
OUT = os.path.join(os.path.dirname(__file__), "..", "ridb", "ridb.json")
CENTERS = [(38.88, -78.50), (38.40, -79.12), (37.75, -79.25), (38.53, -78.44)]
BOX = (-80.2, 37.3, -77.8, 39.2)
# Added 2026-10-05: the app's newer regions. Their facilities go in `byRegion` only, so `facilities`
# (read by older app builds as the George Washington's list) does not change. box = west, south, east,
# north of the region's offline areas (padded a little); centers are searched with the same radius.
REGIONS = {
    "pot": {"box": (-78.95, 38.4, -76.9, 39.85),
            "centers": [(38.998, -77.249), (39.325, -77.739), (39.654, -77.442), (39.65, -78.5), (38.576, -77.343)]},
    "mon": {"box": (-80.75, 37.7, -79.15, 39.35),
            "centers": [(39.07, -79.38), (38.70, -79.53), (38.22, -80.26), (37.95, -80.25)]},
    "gs": {"box": (-84.1, 35.3, -82.85, 35.9),
           "centers": [(35.60, -83.81), (35.69, -83.45), (35.68, -83.10)]},
    "nr": {"box": (-81.25, 37.4, -80.75, 38.3),
           "centers": [(38.12, -81.00), (37.90, -80.95), (37.62, -81.00)]},
}


def text(s, limit=700):
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def facility(f):
    fid = str(f.get("FacilityID"))
    orgs = [o.get("OrgName") for o in f.get("ORGANIZATION", [])]
    return {
        "id": fid, "name": (f.get("FacilityName") or "").title().replace("Nf", "NF"),
        "type": f.get("FacilityTypeDescription"), "orgs": orgs,
        "lat": f.get("FacilityLatitude"), "lon": f.get("FacilityLongitude"), "reservable": bool(f.get("Reservable")),
        "enabled": bool(f.get("Enabled", True)),
        "description": text(f.get("FacilityDescription")),
        "directions": text(f.get("FacilityDirections"), 400),
        "phone": f.get("FacilityPhone") or None,
        "stayLimit": f.get("StayLimit") or None,
        "url": f"https://www.recreation.gov/camping/campgrounds/{fid}" if f.get("FacilityTypeDescription") == "Campground"
               else f"https://www.recreation.gov/search?q={urllib.request.quote(f.get('FacilityName') or '')}",
        "activities": sorted({a.get("ActivityName", "").title() for a in f.get("ACTIVITY", []) if a.get("ActivityName")})[:12],
    }


def query(key, centers, box, seen):
    """Add the facilities within `box` around `centers` to `seen` (by id)."""
    for lat, lon in centers:
        url = (f"https://ridb.recreation.gov/api/v1/facilities?latitude={lat}&longitude={lon}"
               f"&radius=35&limit=50&full=true")
        req = urllib.request.Request(url, headers={"User-Agent": UA, "apikey": key, "accept": "application/json"})
        data = json.loads(get(req))
        for f in data.get("RECDATA", []):
            fid = str(f.get("FacilityID"))
            la, lo = f.get("FacilityLatitude"), f.get("FacilityLongitude")
            if fid in seen or not la or not lo:
                continue
            if not (box[1] <= la <= box[3] and box[0] <= lo <= box[2]):
                continue
            seen[fid] = facility(f)


def get(req):
    return urllib.request.urlopen(req, timeout=60).read()


def ordered(seen):
    return sorted(seen.values(), key=lambda x: (x["type"] != "Campground", x["name"]))


def main():
    key = os.environ.get("RIDB_API_KEY", "").strip()
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT))
        except ValueError:
            old = {}
    if not key:
        out["status"] = "no-key"
    else:
        try:
            seen = {}
            query(key, CENTERS, BOX, seen)
            out["status"] = "ok"
            out["facilities"] = ordered(seen)
        except Exception as e:
            out["status"] = f"error: {type(e).__name__}"
            if old.get("status") == "ok":
                out = {**old, "status": "stale", "staleSince": out["fetchedAt"]}
        if out["status"] == "ok":
            # Newer regions: one failing region keeps its last list and drops out of `coverage`
            # only if it never had one, so the app never claims coverage it does not have.
            by_region, coverage = {}, ["gwnf"]
            for rid, cfg in REGIONS.items():
                try:
                    seen = {}
                    query(key, cfg["centers"], cfg["box"], seen)
                    by_region[rid] = ordered(seen)
                    coverage.append(rid)
                except Exception:
                    prev = (old.get("byRegion") or {}).get(rid)
                    if prev is not None:
                        by_region[rid] = prev
                        coverage.append(rid)
            out["byRegion"] = by_region
            out["coverage"] = coverage
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    fac = out.get("facilities") or []
    from collections import Counter
    print({"status": out["status"], "n": len(fac), "types": dict(Counter(f["type"] for f in fac)),
           "byRegion": {r: len(v) for r, v in (out.get("byRegion") or {}).items()}, "coverage": out.get("coverage")})
    for f in fac[:30]:
        print("  ", f["type"], "|", f["name"], "|", f["orgs"], "| reservable" if f["reservable"] else "")


if __name__ == "__main__":
    sys.exit(main())
