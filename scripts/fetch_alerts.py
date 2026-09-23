#!/usr/bin/env python3
"""Park, Parkway and forest alerts for the Mossback app, published to this
repo's GitHub Pages as alerts/alerts.json so phones can fetch (and save)
them without an API key.

- Shenandoah NP, Blue Ridge Parkway and the Appalachian Trail: NPS Data API
  `developer.nps.gov/api/v1/alerts` (key from the NPS_API_KEY secret; public
  domain). Without a key this part is skipped and marked so.
- George Washington & Jefferson NF: its alerts page
  (fs.usda.gov/r08/gwj/alerts), which has no feed. Each alert is tagged
  "ours" (names a place in the app's Lee, North River or Glenwood-Pedlar
  districts or the A.T.), "forestwide" (a rule for the whole forest) or
  "elsewhere".
Run by .github/workflows/alerts.yml every 3 hours.
"""
import html, json, os, re, sys, urllib.request
from datetime import datetime, timezone

UA = "Mossback alerts (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
OUT = os.path.join(os.path.dirname(__file__), "..", "alerts", "alerts.json")

OURS = ["lee ranger", "north river", "glenwood", "pedlar", "massanutten", "elizabeth furnace", "signal knob",
        "powells fort", "powell's fort", "taskers gap", "peters mill", "edinburg gap", "camp roosevelt", "wolf gap",
        "trout pond", "big schloss", "tibbet", "brandywine", "hone quarry", "todd lake", "north river gorge",
        "elkhorn", "hearthstone", "blue hole", "ramsey", "wild oak", "shenandoah mountain", "reddish knob",
        "sherando", "crabtree", "saint mary", "st. mary", "st mary", "mount pleasant", "cave mountain lake",
        "oronoco", "james river footbridge", "appalachian trail", "three ridges", "priest", "spy rock",
        "lynchburg reservoir", "pedlar river", "hidden valley", "bald mountain", "high knob", "fridley"]
# Places in the forest's other districts; these win over a bare "Appalachian Trail".
ELSEWHERE = ["peters mountain", "peter's mountain", "mount rogers", "clinch", "creeper", "eastern divide",
             "grindstone", "powell mountain", "bolar", "moomaw", "warm springs", "james river ranger"]
FORESTWIDE = ["general prohibitions", "food storage", "wilderness areas", "target shooting", "shooting ranges",
              "controlled substances", "designated recreation sites", "motor vehicle operators", "fire restrictions",
              "fire danger", "burn ban"]


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60).read()


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def nps(key):
    if not key:
        return {"status": "no-key", "alerts": []}
    data = json.loads(get("https://developer.nps.gov/api/v1/alerts?parkCode=shen,blri,appa&limit=200",
                          {"X-Api-Key": key}))
    alerts = []
    for a in data.get("data", []):
        alerts.append({"id": a.get("id"), "park": a.get("parkCode"), "title": a.get("title", "").strip(),
                       "category": a.get("category"), "description": a.get("description", "").strip(),
                       "url": a.get("url") or None, "updated": a.get("lastIndexedDate")})
    return {"status": "ok", "alerts": alerts}


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


def main():
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for name, fn in (("nps", lambda: nps(os.environ.get("NPS_API_KEY", "").strip())), ("forest", forest)):
        try:
            out[name] = fn()
        except Exception as e:  # keep the other half if one source fails
            out[name] = {"status": f"error: {type(e).__name__}", "alerts": []}
    # Keep the last good copy of a source that failed this time.
    if os.path.exists(OUT):
        old = json.load(open(OUT))
        for name in ("nps", "forest"):
            if out[name]["status"].startswith("error") and old.get(name, {}).get("status") == "ok":
                out[name] = {**old[name], "status": "stale", "staleSince": out["fetchedAt"]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    print({k: (v["status"], len(v["alerts"])) for k, v in out.items() if isinstance(v, dict)})


if __name__ == "__main__":
    sys.exit(main())
