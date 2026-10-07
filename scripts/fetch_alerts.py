#!/usr/bin/env python3
"""Park, Parkway and forest alerts for the Mossback app, published to this
repo's GitHub Pages as alerts/alerts.json so phones can fetch (and save)
them without an API key.

- Every region's National Park Service units (Shenandoah, the Parkway, the
  Potomac units, the Smokies, New River, Zion and the rest) and the
  Appalachian Trail: NPS Data API `developer.nps.gov/api/v1/alerts` (key
  from the NPS_API_KEY secret; public domain). The park codes come from
  conditions_regions.json beside this script, the app registry's export
  (plan 3.4, 2026-10-07; fetch_conditions.py reads the same file), fetched
  in batches of NPS_BATCH codes with every page, so a new park is a registry
  entry and a copy of the export, never a code change here. `nps.parks`
  lists the codes that answered (the app says "not connected yet" for a
  region whose code is missing), `nps.errors` the batches that did not.
  Beside the main file, alerts/regions/<id>.json carries each region's own
  park alerts (`{fetchedAt, region, nps: {status, parks, alerts}}`), listed
  in the main file's `regionFiles`; the app prefers a region's file for its
  parks when present, and the main file keeps every code until
  MAIN_FILE_PARKS narrows it. Without a key this part is skipped and marked so.
- George Washington & Jefferson NF: its alerts page
  (fs.usda.gov/r08/gwj/alerts), which has no feed. Each alert is tagged
  "ours" (names a place in the app's Lee, North River, Glenwood-Pedlar,
  Eastern Divide, Warm Springs, James River or Mount Rogers districts or the A.T.), "forestwide" (a rule for the whole forest) or
  "elsewhere".
Run by .github/workflows/alerts.yml every 3 hours.
"""
import html, json, os, re, sys, urllib.request
from datetime import datetime, timezone

UA = "Mossback alerts (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "alerts", "alerts.json")
REGIONS_OUT = os.path.join(HERE, "..", "alerts", "regions")
REGIONS_FILE = os.path.join(HERE, "conditions_regions.json")
# The park codes whose alerts stay in the main alerts.json. None: every code (the feed is additive while
# builds that read only the main file are out). Set it to NPS_PARKS (the twelve old codes) once build 11,
# which folds in the per-region files, is on the App Store; the other parks' alerts then live only in
# alerts/regions/<id>.json and the main file shrinks (lead's rule, 2026-10-07).
MAIN_FILE_PARKS = None

OURS = ["lee ranger", "north river", "glenwood", "pedlar", "massanutten", "elizabeth furnace", "signal knob",
        "powells fort", "powell's fort", "taskers gap", "peters mill", "edinburg gap", "camp roosevelt", "wolf gap",
        "trout pond", "big schloss", "tibbet", "brandywine", "hone quarry", "todd lake", "north river gorge",
        "elkhorn", "hearthstone", "blue hole", "ramsey", "wild oak", "shenandoah mountain", "reddish knob",
        "sherando", "crabtree", "saint mary", "st. mary", "st mary", "mount pleasant", "cave mountain lake",
        "oronoco", "james river footbridge", "appalachian trail", "three ridges", "priest", "spy rock",
        "lynchburg reservoir", "pedlar river", "hidden valley", "bald mountain", "high knob", "fridley",
        # Jefferson NF, Eastern Divide district (in the app since 2026-10-05).
        "eastern divide", "mcafee", "dragons tooth", "dragon's tooth", "tinker cliffs", "little stony",
        "cascades recreation", "the cascades", "mountain lake", "peters mountain", "peter's mountain",
        "brush mountain", "audie murphy", "barney's wall", "barneys wall", "pandapas", "dismal falls",
        "dismal creek", "wind rock", "kelly knob", "angels rest", "sinking creek", "war spur", "keffer oak",
        "fenwick mines", "roaring run", "craig creek", "andy layne", "sugar run", "rice field", "chestnut knob",
        "hay rock", "catawba",
        # Warm Springs, James River and Mount Rogers districts (in the app since 2026-10-05).
        "warm springs ranger", "james river ranger", "mount rogers", "massie gap", "grayson highlands",
        "wilburn ridge", "rhododendron gap", "elk garden", "whitetop", "buzzard rock", "fox creek", "beartree",
        "hurricane campground", "hurricane creek", "comers rock", "comers creek", "virginia creeper",
        "iron mountain trail", "konnarock", "lewis fork", "little wilson creek", "little dry run",
        "raccoon branch", "straight branch", "trimpi", "partnership shelter", "saunders shelter", "damascus",
        "dickey gap", "dickey knob", "teas road", "grindstone", "lake moomaw", "bolar", "moomaw",
        "jackson river", "back creek", "blowing springs", "laurel fork", "locust springs", "poor farm",
        "rough mountain", "greenwood point", "lost woman", "walton tract", "beards mountain", "rich hole",
        "dolly ann", "longdale", "green pastures", "coles point", "morris hill", "fortney", "kelly bridge",
        "cocks comb", "white rock tower", "pete's cave", "allegheny trail", "covington", "clifton forge",
        "children's forest", "fore mountain", "mcallister", "gathright"]
# Places in the forest's other districts; these win over a bare "Appalachian Trail".
ELSEWHERE = ["clinch", "powell mountain"]
FORESTWIDE = ["general prohibitions", "food storage", "wilderness areas", "target shooting", "shooting ranges",
              "controlled substances", "designated recreation sites", "motor vehicle operators", "fire restrictions",
              "fire danger", "burn ban"]


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60).read()


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# The codes the job fetched before the registry's export drove it (Shenandoah, the Parkway, the A.T., the
# Potomac & DC units, the Smokies, New River): the fallback when conditions_regions.json is missing.
NPS_PARKS = ["shen", "blri", "appa", "choh", "hafe", "cato", "prwi", "gwmp", "grsm", "neri", "gari", "blue"]
# Not a region of its own, but Shenandoah's and the forest's Appalachian Trail hikes read its alerts.
EXTRA_NPS_PARKS = ["appa"]
NPS_BATCH = 10  # park codes per request
NPS_LIMIT = 500  # alerts per page; the API pages with start=


def park_codes(path=REGIONS_FILE):
    """Every region's NPS park codes from the registry's export, in registry order and without repeats
    (the Parkway's code is the Parkway's and the Pisgah's), plus the A.T.; the old list when the export is missing."""
    try:
        with open(path) as f:
            regions = json.load(f)["regions"]
    except (OSError, ValueError, KeyError, TypeError):
        return list(NPS_PARKS)
    out = []
    for r in regions:
        for code in (r.get("alerts") or {}).get("npsParks") or []:
            if code not in out:
                out.append(code)
    for code in EXTRA_NPS_PARKS:
        if code not in out:
            out.append(code)
    return out


def nps_batch(key, codes):
    """One batch of park codes, every page of it."""
    alerts, start = [], 0
    while True:
        url = (f"https://developer.nps.gov/api/v1/alerts?parkCode={','.join(codes)}"
               f"&limit={NPS_LIMIT}&start={start}")
        data = json.loads(get(url, {"X-Api-Key": key}))
        page = data.get("data") or []
        for a in page:
            alerts.append({"id": a.get("id"), "park": a.get("parkCode"), "title": a.get("title", "").strip(),
                           "category": a.get("category"), "description": a.get("description", "").strip(),
                           "url": a.get("url") or None, "updated": a.get("lastIndexedDate")})
        total = int(data.get("total") or len(page))
        start += NPS_LIMIT
        if not page or start >= total:
            return alerts


def nps(key, codes=None):
    """Alerts for every park code, NPS_BATCH codes a request: `parks` are the codes whose batch answered
    (the app shows a region's "no current alerts" only when its code is there), `errors` the batches that did not."""
    if not key:
        return {"status": "no-key", "alerts": []}
    codes = park_codes() if codes is None else list(codes)
    alerts, parks, errors = [], [], {}
    for i in range(0, len(codes), NPS_BATCH):
        batch = codes[i:i + NPS_BATCH]
        try:
            alerts += nps_batch(key, batch)
            parks += batch
        except Exception as e:
            errors[",".join(batch)] = f"error: {type(e).__name__}"
    if not parks:
        return {"status": "error: no batch answered", "alerts": [], "parks": [], "errors": errors}
    out = {"status": "ok", "parks": parks, "alerts": alerts}
    if errors:
        out["errors"] = errors
    return out


def forest():
    page = get("https://www.fs.usda.gov/r08/gwj/alerts").decode("utf-8", "ignore")
    alerts = []
    for card in re.findall(r'<li class="usa-card[^"]*wfs-alert-flag([^"]*)">(.*?)</li>', page, flags=re.S):
        level, body = card[0].strip(), card[1]
        m = re.search(r'<a href="(/r08/gwj/alerts/[^"]+)"[^>]*>(.*?)</a>', body, flags=re.S)
        if not m:
            continue
        title = text(m.group(2))
        summary = re.search(r'<div class="usa-card__body">(.*?)</div>', body, flags=re.S)
        start = re.search(r"Alert Start Date:</strong>\s*([^<]+)<", body)
        order = re.search(r"Forest Order:</strong>\s*([^<]+)<", body)
        blob = f"{title} {text(summary.group(1)) if summary else ''}".lower()
        scope = ("elsewhere" if any(k in blob for k in ELSEWHERE)
                 else "ours" if any(k in blob for k in OURS)
                 else "forestwide" if any(k in title.lower() for k in FORESTWIDE) else "elsewhere")
        alerts.append({"title": title, "level": level or "information", "summary": text(summary.group(1)) if summary else "",
                       "url": "https://www.fs.usda.gov" + m.group(1), "start": start.group(1).strip() if start else None,
                       "order": order.group(1).strip() if order else None, "scope": scope})
    return {"status": "ok" if alerts else "empty", "alerts": alerts}


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def stale_part(new, old, key, now):
    """Keep the last good copy of a part that failed this time, marked stale."""
    if str(new.get(key, {}).get("status", "")).startswith("error") and (old or {}).get(key, {}).get("status") in ("ok", "stale"):
        new[key] = {**old[key], "status": "stale", "staleSince": now}
    return new


def region_file(now, region, nps_out):
    """One region's alerts file: the NPS alerts of its park codes, and which of its codes answered.
    The app prefers it to the main file for those parks when it is present (plan 3.4, per-region files)."""
    codes = (region.get("alerts") or {}).get("npsParks") or []
    status = str(nps_out.get("status", "error"))
    answered = [c for c in codes if c in (nps_out.get("parks") or [])]
    if status == "ok" and codes and not answered:
        status = "error: none of %s answered" % ",".join(codes)
    return {"fetchedAt": now, "region": region["id"],
            "nps": {"status": status, "parks": answered,
                    "alerts": [a for a in (nps_out.get("alerts") or []) if a.get("park") in answered]}}


def assemble(now, parts, regions, old_main=None, old_region=None):
    """The main file (every source, every code unless MAIN_FILE_PARKS narrows it) and one file per region
    that has park codes; `old_main` and `old_region(id)` are the last published copies, for stale fallbacks."""
    old_region = old_region or (lambda rid: None)
    nps_out = parts.get("nps") or {"status": "error", "alerts": []}
    files = {}
    for r in regions:
        if not (r.get("alerts") or {}).get("npsParks"):
            continue
        files[r["id"]] = stale_part(region_file(now, r, nps_out), old_region(r["id"]), "nps", now)
    main_nps = nps_out
    if MAIN_FILE_PARKS is not None and nps_out.get("status") == "ok":
        keep = [c for c in nps_out.get("parks") or [] if c in MAIN_FILE_PARKS]
        main_nps = {**nps_out, "parks": keep, "alerts": [a for a in nps_out.get("alerts") or [] if a.get("park") in keep]}
    out = {"fetchedAt": now, "nps": main_nps, "forest": parts.get("forest") or {"status": "error", "alerts": []},
           "regionFiles": sorted(files)}
    for key in ("nps", "forest"):
        stale_part(out, old_main, key, now)
    return out, files


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = {}
    for name, fn in (("nps", lambda: nps(os.environ.get("NPS_API_KEY", "").strip())), ("forest", forest)):
        try:
            parts[name] = fn()
        except Exception as e:  # keep the other half if one source fails
            parts[name] = {"status": f"error: {type(e).__name__}", "alerts": []}
    regions = []
    try:
        with open(REGIONS_FILE) as f:
            regions = json.load(f)["regions"]
    except (OSError, ValueError, KeyError):
        print("no conditions_regions.json beside the script: main file only", file=sys.stderr)
    out, files = assemble(now, parts, regions, read_json(OUT),
                          lambda rid: read_json(os.path.join(REGIONS_OUT, f"{rid}.json")))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    if files:
        os.makedirs(REGIONS_OUT, exist_ok=True)
        for rid, data in files.items():
            with open(os.path.join(REGIONS_OUT, f"{rid}.json"), "w") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
    print({k: (v["status"], len(v["alerts"])) for k, v in out.items() if isinstance(v, dict)})
    print("region files:", {rid: (d["nps"]["status"], len(d["nps"]["parks"]), len(d["nps"]["alerts"])) for rid, d in files.items()})


if __name__ == "__main__":
    sys.exit(main())
