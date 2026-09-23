#!/usr/bin/env python3
"""The national forest's campgrounds (and other Recreation.gov facilities)
around the app's three George Washington NF districts, from Recreation.gov's
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


def text(s, limit=700):
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def main():
    key = os.environ.get("RIDB_API_KEY", "").strip()
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if not key:
        out["status"] = "no-key"
    else:
        try:
            seen = {}
            for lat, lon in CENTERS:
                url = (f"https://ridb.recreation.gov/api/v1/facilities?latitude={lat}&longitude={lon}"
                       f"&radius=35&limit=50&full=true")
                req = urllib.request.Request(url, headers={"User-Agent": UA, "apikey": key, "accept": "application/json"})
                data = json.loads(urllib.request.urlopen(req, timeout=60).read())
                for f in data.get("RECDATA", []):
                    fid = str(f.get("FacilityID"))
                    la, lo = f.get("FacilityLatitude"), f.get("FacilityLongitude")
                    if fid in seen or not la or not lo:
                        continue
                    if not (BOX[1] <= la <= BOX[3] and BOX[0] <= lo <= BOX[2]):
                        continue
                    orgs = [o.get("OrgName") for o in f.get("ORGANIZATION", [])]
                    seen[fid] = {
                        "id": fid, "name": (f.get("FacilityName") or "").title().replace("Nf", "NF"),
                        "type": f.get("FacilityTypeDescription"), "orgs": orgs,
                        "lat": la, "lon": lo, "reservable": bool(f.get("Reservable")),
                        "enabled": bool(f.get("Enabled", True)),
                        "description": text(f.get("FacilityDescription")),
                        "directions": text(f.get("FacilityDirections"), 400),
                        "phone": f.get("FacilityPhone") or None,
                        "stayLimit": f.get("StayLimit") or None,
                        "url": f"https://www.recreation.gov/camping/campgrounds/{fid}" if f.get("FacilityTypeDescription") == "Campground"
                               else f"https://www.recreation.gov/search?q={urllib.request.quote(f.get('FacilityName') or '')}",
                        "activities": sorted({a.get("ActivityName", "").title() for a in f.get("ACTIVITY", []) if a.get("ActivityName")})[:12],
                    }
            out["status"] = "ok"
            out["facilities"] = sorted(seen.values(), key=lambda x: (x["type"] != "Campground", x["name"]))
        except Exception as e:
            out["status"] = f"error: {type(e).__name__}"
            if os.path.exists(OUT):
                old = json.load(open(OUT))
                if old.get("status") == "ok":
                    out = {**old, "status": "stale", "staleSince": out["fetchedAt"]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    fac = out.get("facilities") or []
    from collections import Counter
    print({"status": out["status"], "n": len(fac), "types": dict(Counter(f["type"] for f in fac))})
    for f in fac[:30]:
        print("  ", f["type"], "|", f["name"], "|", f["orgs"], "| reservable" if f["reservable"] else "")


if __name__ == "__main__":
    sys.exit(main())
