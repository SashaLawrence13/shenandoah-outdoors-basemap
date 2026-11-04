#!/usr/bin/env python3
"""Pisgah and Nantahala NF alerts (2026-10-05): a standalone copy of the forest half of
data-pipeline/alerts/fetch_alerts.py for the new region; the existing job is
not edited. The Mon NF's alerts page (fs.usda.gov/r08/northcarolina/alerts) has
the same template as the GW&J's (cards with wfs-alert-flag classes, a
"Forest Order" and "Alert Start Date" line), so the same parser applies. No
feed exists. Each alert is tagged "ours" (names a place in the region),
"forestwide" (a rule for the whole forest) or "ours-road" (a road closure naming
no place the app has).

The wiring step would add `pisgah()` (below) as a third source in the
alerts job, with PLACES in its `OURS` list. Run standalone, it prints the
current alerts and saves them to data-pipeline/raw/usfs/2026-10-05/pisgah/alerts_snapshot.json.
"""
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh) data-pipeline"
URL = "https://www.fs.usda.gov/r08/northcarolina/alerts"
BASE = "https://www.fs.usda.gov"
PLACES = ["pisgah", "nantahala", "grandfather", "appalachian ranger", "cheoah", "tusquittee", "linville", "wilson creek", "table rock", "hawksbill",
          "black balsam", "shining rock", "graveyard", "looking glass", "davidson river", "max patch", "roan", "mount mitchell", "craggy", "big ivy",
          "commissary", "bent creek", "mills river", "curtis creek", "old fort", "catawba", "panthertown", "standing indian", "wayah", "joyce kilmer",
          "slickrock", "whitewater", "chattooga", "ellicott", "tsali", "fires creek", "hot springs", "paint rock", "shope creek", "bradley creek",
          "blue ridge parkway", "art loeb", "bartram", "appalachian trail", "mountains-to-sea", "benton mackaye", "helene"]
OTHER_FOREST = ["croatan", "uwharrie", "fishers landing", "hurricane florence"]
FORESTWIDE = ["general prohibitions", "food storage", "wilderness areas", "target shooting", "shooting ranges",
              "controlled substances", "designated recreation sites", "motor vehicle operators", "fire restrictions",
              "fire danger", "burn ban", "forest-wide", "forestwide"]


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def pisgah():
    page = get(URL).decode("utf-8", "ignore")
    alerts = []
    for level, body in re.findall(r'<li class="usa-card[^"]*wfs-alert-flag([^"]*)">(.*?)</li>', page, flags=re.S):
        m = re.search(r'<a href="(/r08/northcarolina/alerts/[^"]+)"[^>]*>(.*?)</a>', body, flags=re.S)
        if not m:
            continue
        title = text(m.group(2))
        summary = re.search(r'<div class="usa-card__body">(.*?)</div>', body, flags=re.S)
        start = re.search(r"Alert Start Date:</strong>\s*([^<]+)<", body)
        order = re.search(r"Forest Order:</strong>\s*([^<]+)<", body)
        blob = f"{title} {text(summary.group(1)) if summary else ''}".lower()
        # Every alert on this page is the Pisgah and Nantahala's own (one forest, no
        # other forest's places appear), so only the general rules are
        # "forestwide"; PLACES decides whether a road or area item names a place
        # the app has ("ours") or only a road number ("ours-road").
        if any(k in blob for k in OTHER_FOREST):
            continue  # Croatan and Uwharrie are not in this region
        scope = ("forestwide" if any(k in title.lower() for k in FORESTWIDE)
                 else "ours" if any(k in blob for k in PLACES) else "ours-road" if re.search(r"\b(road|fr|nfs)\b", blob) else "ours")
        alerts.append({"title": title, "level": level.strip() or "information", "summary": text(summary.group(1)) if summary else "",
                       "url": BASE + m.group(1), "start": start.group(1).strip() if start else None,
                       "order": order.group(1).strip() if order else None, "scope": scope})
    return {"status": "ok" if alerts else "empty", "alerts": alerts}


def main():
    res = pisgah()
    res["fetchedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = os.path.join(root, "data-pipeline/raw/usfs/2026-10-05/pisgah/alerts_snapshot.json")
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print(res["status"], len(res["alerts"]))
    for a in res["alerts"]:
        print(" -", a["scope"], "|", a["level"], "|", a["title"], "|", a["start"])


if __name__ == "__main__":
    sys.exit(main())
