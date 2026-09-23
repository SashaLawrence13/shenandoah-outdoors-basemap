#!/usr/bin/env python3
"""Shenandoah NP and Blue Ridge Parkway details for the Mossback app, from
the NPS Data API (public domain; key in the NPS_API_KEY secret), published
as park/park.json on this repo's Pages:

- webcams: the API's list plus the still images the park links from its
  webcam pages, each checked for whether it's publishing right now
- events: ranger programs and events for the next 60 days
- campgrounds: seasons, fees, reservations, sites and amenities
- visitorCenters: hours, with the NPS-listed exceptions (closures)
- thingsToDo and tours: the park's own activity pages
Run by .github/workflows/alerts.yml with the alerts.
"""
import html, json, os, re, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

UA = "Mossback park data (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
OUT = os.path.join(os.path.dirname(__file__), "..", "park", "park.json")
KEY = os.environ.get("NPS_API_KEY", "").strip()
PARKS = "shen,blri"

# Still images the park's webcam pages link to (the API lists the pages,
# not always the image). Checked every run; "offline" when they don't answer.
STILLS = [
    {"id": "big-meadows", "title": "Big Meadows", "park": "shen",
     "image": "https://www.nps.gov/webcams-shen/bvc2_800.jpg",
     "page": "https://www.nps.gov/shen/learn/photosmultimedia/bm_webcam.htm",
     "credit": "National Park Service", "latitude": 38.5312, "longitude": -78.4381},
    {"id": "phenocam", "title": "Forest canopy (PhenoCam)", "park": "shen",
     "image": "https://phenocam.nau.edu/data/latest/shenandoah.jpg",
     "page": "https://phenocam.nau.edu/webcam/sites/shenandoah/",
     "credit": "PhenoCam Network (CC BY 4.0)", "latitude": 38.6178, "longitude": -78.3503},
]
STREAMS = [
    {"id": "big-meadows-live", "title": "Big Meadows (live video)", "park": "shen",
     "page": "https://www.nps.gov/shen/learn/photosmultimedia/bigmeadows_livecam.htm"},
    {"id": "valley-live", "title": "Shenandoah Valley (live video)", "park": "shen",
     "page": "https://www.nps.gov/shen/learn/photosmultimedia/shenvalleycam.htm"},
]


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def api(endpoint, **params):
    q = "&".join(f"{k}={v}" for k, v in {"parkCode": PARKS, "limit": 200, **params}.items())
    return json.loads(get(f"https://developer.nps.gov/api/v1/{endpoint}?{q}", {"X-Api-Key": KEY}).read())["data"]


def text(s, limit=600):
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def still_status(cam):
    try:
        r = get(cam["image"])
        body = r.read(64)
        ok = r.status == 200 and body[:2] == b"\xff\xd8"  # a JPEG
        return {"status": "live" if ok else "offline", "updated": r.headers.get("Last-Modified")}
    except Exception:
        return {"status": "offline", "updated": None}


def hours(oh):
    out = []
    for h in oh or []:
        out.append({"name": h.get("name"), "description": text(h.get("description"), 300),
                    "standard": h.get("standardHours") or {},
                    "exceptions": [{"name": e.get("name"), "start": e.get("startDate"), "end": e.get("endDate"),
                                    "hours": e.get("exceptionHours") or {}} for e in h.get("exceptions") or []]})
    return out


def main():
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out["webcams"] = [{**c, **still_status(c), "kind": "still"} for c in STILLS] + \
                     [{**c, "kind": "stream", "status": "link"} for c in STREAMS]
    if not KEY:
        out["status"] = "no-key"
    else:
        out["status"] = "ok"
        try:
            # Webcams the API knows that aren't already listed above.
            known = {c["page"] for c in STILLS + STREAMS}
            for w in api("webcams"):
                if w.get("url") in known:
                    continue
                out["webcams"].append({"id": w.get("id"), "title": w.get("title"), "park": (w.get("relatedParks") or [{}])[0].get("parkCode"),
                                       "page": w.get("url"), "kind": "stream" if w.get("isStreaming") else "page",
                                       "status": "link", "description": text(w.get("description"), 200)})
            today = date.today()
            events = api("events", dateStart=today.isoformat(), dateEnd=(today + timedelta(days=60)).isoformat(), pageSize=200)
            out["events"] = [{"id": e.get("id"), "park": e.get("sitecode"), "title": text(e.get("title"), 120),
                              "dates": (e.get("dates") or [])[:30], "times": e.get("times") or [],
                              "location": text(e.get("location"), 160), "free": e.get("isfree") in (True, "true"),
                              "fee": text(e.get("feeinfo"), 160), "category": e.get("category"),
                              "types": e.get("types") or [], "description": text(e.get("description"), 500),
                              "latitude": num(e.get("latitude")), "longitude": num(e.get("longitude")),
                              "url": e.get("infourl") or None, "registration": e.get("isregresrequired") in (True, "true")}
                             for e in events]
            out["campgrounds"] = [{"id": c.get("id"), "park": c.get("parkCode"), "name": c.get("name"),
                                   "description": text(c.get("description"), 400),
                                   "latitude": num(c.get("latitude")), "longitude": num(c.get("longitude")),
                                   "reservation": text(c.get("reservationInfo"), 300), "reservationUrl": c.get("reservationUrl") or None,
                                   "hours": hours(c.get("operatingHours")),
                                   "fees": [{"title": f.get("title"), "cost": f.get("cost"), "description": text(f.get("description"), 200)} for f in c.get("fees") or []],
                                   "sites": c.get("campsites") or {}, "url": c.get("url") or None,
                                   "amenities": {k: v for k, v in (c.get("amenities") or {}).items() if v not in ("", [], "No", None)}}
                                  for c in api("campgrounds")]
            out["visitorCenters"] = [{"id": v.get("id"), "park": v.get("parkCode"), "name": v.get("name"),
                                      "description": text(v.get("description"), 300),
                                      "latitude": num(v.get("latitude")), "longitude": num(v.get("longitude")),
                                      "hours": hours(v.get("operatingHours")), "url": v.get("url") or None}
                                     for v in api("visitorcenters")]
            out["thingsToDo"] = [{"id": t.get("id"), "title": t.get("title"), "summary": text(t.get("shortDescription"), 300),
                                  "duration": t.get("duration"), "url": t.get("url"),
                                  "latitude": num(t.get("latitude")), "longitude": num(t.get("longitude"))}
                                 for t in api("thingstodo")]
            out["tours"] = [{"id": t.get("id"), "title": t.get("title"), "description": text(t.get("description"), 400),
                             "duration": f"{t.get('durationMin')}–{t.get('durationMax')} {t.get('durationUnit')}".strip(),
                             "stops": [s.get("assetName") for s in t.get("stops") or []]}
                            for t in api("tours")]
        except Exception as e:  # keep the last good copy rather than publish a half
            out["status"] = f"error: {type(e).__name__}"
            if os.path.exists(OUT):
                old = json.load(open(OUT))
                if old.get("status") == "ok":
                    old["webcams"] = out["webcams"]
                    old["status"] = "stale"
                    old["staleSince"] = out["fetchedAt"]
                    out = old
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    print({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})


if __name__ == "__main__":
    sys.exit(main())
