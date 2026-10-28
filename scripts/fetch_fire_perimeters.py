#!/usr/bin/env python3
"""Current wildfire perimeters per Mossback region (NIFC WFIGS), for the app's "Fire nearby" notice.

Source: the National Interagency Fire Center's public ArcGIS service of current interagency fire
perimeters (WFIGS, no key, public domain U.S. government data):
https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0
One query for the whole country (about 100 to 300 perimeters, simplified to roughly 200 m), then every
perimeter whose bounding box touches a region's fireBox (its box padded by firePadMiles, from
scripts/conditions_regions.json, the same box the satellite fire checks use) goes in that region's file.

Writes conditions/fire/<region>.json for every region, fires or not, so "no perimeter near here" is
told apart from "no file":
  {"fetchedAt", "source": "nifc-wfigs", "region", "box", "status": "ok",
   "fires": [{"id", "name", "type" (WF wildfire, RX prescribed, CX complex), "acres", "contained",
              "updated", "perimeterAt", "discovered", "center": [lon, lat], "bbox", "geometry"}]}
`contained` and `acres` are null when the incident has not reported them; times are ISO UTC.
Plus conditions/fire/index.json: {"fetchedAt", "source", "check", "regions": {id: count}}.

Refresh window: each run first asks the service for three numbers (perimeter count, latest record
change, latest perimeter date). When they match index.json's `check` and the files are under
MAX_AGE_HOURS old, nothing else is fetched or written. Any failure keeps the last good files and exits 0.

Fire restriction stages: the Forest Service publishes no machine-readable restriction stages for the
app's national forests (George Washington & Jefferson, Monongahela, Pisgah & Nantahala); those forests
post fire restrictions and burn bans as forest orders on their alerts pages, which fetch_alerts.py and
fetch_alerts_monongahela.py already read. Only a few western forests publish ArcGIS layers of their own.
So there is no restrictions part here (README, 2026-10-28).
Run by .github/workflows/alerts.yml. Tests: python3 -m unittest test_fetch_fire_perimeters
"""
import json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

SERVICE = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
           "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query")
UA = "Mossback conditions (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS_FILE = os.path.join(HERE, "conditions_regions.json")
OUT = os.path.join(HERE, "..", "conditions", "fire")
FIELDS = ["attr_UniqueFireIdentifier", "attr_IncidentName", "poly_IncidentName", "attr_IncidentTypeCategory",
          "attr_IncidentSize", "poly_GISAcres", "attr_PercentContained", "attr_ModifiedOnDateTime_dt",
          "poly_DateCurrent", "attr_FireDiscoveryDateTime"]
SIMPLIFY_DEG = 0.002  # about 200 m, asked of the server
OUTLINE_DEG = 0.003  # about 300 m: the outline the app draws, simplified again here (Douglas-Peucker)
PRECISION = 4
PAGE = 1000
MAX_AGE_HOURS = 24  # rewrite at least daily even when nothing changed, so "checked" stays honest


def get_json(params):
    url = SERVICE + "?" + urllib.parse.urlencode({**params, "f": params.get("f", "json")})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = json.loads(urllib.request.urlopen(req, timeout=90).read())
    if isinstance(data, dict) and "error" in data:  # ArcGIS answers errors with HTTP 200
        raise RuntimeError("service error %s" % (data["error"] or {}).get("code"))
    return data


def check():
    """[count, latest record change, latest perimeter date]: changes whenever any perimeter does."""
    stats = [{"statisticType": t, "onStatisticField": f, "outStatisticFieldName": n} for t, f, n in
             (("count", "OBJECTID", "n"), ("max", "attr_ModifiedOnDateTime_dt", "m"), ("max", "poly_DateCurrent", "p"))]
    a = get_json({"where": "1=1", "outStatistics": json.dumps(stats)})["features"][0]["attributes"]
    return [a.get("n"), a.get("m"), a.get("p")]


def perimeters():
    out, offset = [], 0
    while True:
        page = get_json({"where": "1=1", "outFields": ",".join(FIELDS), "outSR": 4326,
                         "geometryPrecision": PRECISION, "maxAllowableOffset": SIMPLIFY_DEG,
                         "resultOffset": offset, "resultRecordCount": PAGE, "orderByFields": "OBJECTID",
                         "f": "geojson"})
        feats = page.get("features") or []
        out += feats
        if not feats or not (page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")):
            return out
        offset += len(feats)


def iso(ms):
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bbox(geometry):
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    walk((geometry or {}).get("coordinates") or [])
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def simplify(points, tol=OUTLINE_DEG):
    """Douglas-Peucker on one ring (iterative, so a long ring cannot hit the recursion limit)."""
    if len(points) < 4:
        return points
    keep, stack = {0, len(points) - 1}, [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a][:2], points[b][:2]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        best, at = 0.0, None
        for i in range(a + 1, b):
            px, py = points[i][:2]
            d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm if norm else ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
            if d > best:
                best, at = d, i
        if at is not None and best > tol:
            keep.add(at)
            stack += [(a, at), (at, b)]
    return [points[i] for i in sorted(keep)]


def outline(geometry):
    """The perimeter as a MultiPolygon of outer rings only, simplified and rounded; fragments too small to
    keep a shape are dropped unless nothing else is left. Unburned islands (holes) are not drawn."""
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    rings = [[[round(x, PRECISION), round(y, PRECISION)] for x, y, *_ in simplify(p[0])] for p in polys if p and p[0]]
    kept = [r for r in rings if len(r) >= 4]
    if not kept and rings:
        kept = [max(rings, key=len)]
    return {"type": "MultiPolygon", "coordinates": [[r] for r in kept]}


def name_of(p):
    n = (p.get("attr_IncidentName") or p.get("poly_IncidentName") or "").strip()
    return n.title() if n.isupper() else n


def fire(feature):
    """The app's fields from one WFIGS feature, or None when it has no usable outline."""
    p, g = feature.get("properties") or {}, feature.get("geometry")
    b = bbox(g)
    if not b or g.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    acres = p.get("attr_IncidentSize")
    if not isinstance(acres, (int, float)) or acres <= 0:
        acres = p.get("poly_GISAcres")
    contained = p.get("attr_PercentContained")
    stamps = [t for t in (p.get("attr_ModifiedOnDateTime_dt"), p.get("poly_DateCurrent")) if isinstance(t, (int, float))]
    return {"id": p.get("attr_UniqueFireIdentifier") or name_of(p), "name": name_of(p) or "Unnamed fire",
            "type": p.get("attr_IncidentTypeCategory") or "WF",
            "acres": (round(acres) if acres >= 10 else round(acres, 1)) if isinstance(acres, (int, float)) and acres > 0 else None,
            "contained": round(contained) if isinstance(contained, (int, float)) else None,
            "updated": iso(max(stamps)) if stamps else None, "perimeterAt": iso(p.get("poly_DateCurrent")),
            "discovered": iso(p.get("attr_FireDiscoveryDateTime")),
            "center": [round((b[0] + b[2]) / 2, PRECISION), round((b[1] + b[3]) / 2, PRECISION)],
            "bbox": [round(v, PRECISION) for v in b], "geometry": outline(g)}


def touches(b, box):
    return b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1]


def region_files(fires, regions, stamp):
    files = {}
    for r in regions:
        box = r.get("fireBox") or r["box"]
        mine = sorted((f for f in fires if touches(f["bbox"], box)), key=lambda f: (-(f["acres"] or 0), f["name"]))
        files[r["id"]] = {"fetchedAt": stamp, "source": "nifc-wfigs", "region": r["id"], "box": box,
                          "status": "ok", "fires": mine}
    return files


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def fresh(index, now, hours=MAX_AGE_HOURS):
    try:
        at = datetime.fromisoformat(index["fetchedAt"].replace("Z", "+00:00"))
    except (TypeError, KeyError, AttributeError, ValueError):
        return False
    return (now - at).total_seconds() < hours * 3600


def main(argv=None):
    out = OUT if not argv else argv[0]
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(REGIONS_FILE) as fh:
            regions = json.load(fh)["regions"]
    except (OSError, ValueError, KeyError):
        print("fire: no conditions_regions.json beside the script; nothing written")
        return 0
    index = read_json(os.path.join(out, "index.json")) or {}
    try:
        stats = check()
        if stats == index.get("check") and fresh(index, now) and all(
                os.path.exists(os.path.join(out, f"{r['id']}.json")) for r in regions):
            print("fire: perimeters unchanged since the last run; files kept")
            return 0
        feats = perimeters()
    except Exception as e:  # keep the last good files; say only the error type
        print(f"fire: fetch failed ({type(e).__name__}); last good files kept")
        return 0
    fires = [f for f in (fire(x) for x in feats) if f]
    if stats[0] and len(feats) < stats[0] * 0.9:
        print(f"fire: got {len(feats)} of {stats[0]} perimeters; last good files kept")
        return 0
    files = region_files(fires, regions, stamp)
    os.makedirs(out, exist_ok=True)
    for rid, data in files.items():
        with open(os.path.join(out, f"{rid}.json"), "w") as fh:
            json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
    counts = {rid: len(d["fires"]) for rid, d in files.items()}
    with open(os.path.join(out, "index.json"), "w") as fh:
        json.dump({"fetchedAt": stamp, "source": "nifc-wfigs", "check": stats, "regions": counts}, fh,
                  separators=(",", ":"))
    near = {k: v for k, v in counts.items() if v}
    print(f"fire: {len(fires)} perimeters nationally; regions with any: {near or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
