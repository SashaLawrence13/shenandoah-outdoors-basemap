#!/usr/bin/env python3
"""Monongahela NF alerts (2026-10-05): a standalone copy of the forest half of
data-pipeline/alerts/fetch_alerts.py for the new region; the existing job is
not edited. The Mon NF's alerts page (fs.usda.gov/r09/monongahela/alerts) has
the same template as the GW&J's (cards with wfs-alert-flag classes, a
"Forest Order" and "Alert Start Date" line), so the same parser applies. No
feed exists. Each alert is tagged "ours" (names a place in the region),
"forestwide" (a rule for the whole forest) or "ours-road" (a road closure naming
no place the app has).

The wiring step would add `monongahela()` (below) as a third source in the
alerts job, with PLACES in its `OURS` list. Run standalone, it prints the
current alerts and saves them to data-pipeline/raw/usfs/2026-10-05/monongahela/alerts_snapshot.json.
"""
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh) data-pipeline"
URL = "https://www.fs.usda.gov/r09/monongahela/alerts"
BASE = "https://www.fs.usda.gov"
PLACES = ["dolly sods", "bear rocks", "red creek", "blackbird knob", "seneca rocks", "seneca creek", "spruce knob", "spruce mountain",
          "monongahela", "cheat ranger", "greenbrier ranger", "potomac ranger", "gauley ranger", "marlinton ranger", "white sulphur",
          "cranberry", "otter creek", "laurel fork", "roaring plains", "highland scenic", "falls of hills creek", "gaudineer",
          "blackwater", "canaan", "smoke hole", "fr 75", "fr 19", "forest road 75", "forest road 19", "allegheny trail",
          "tea creek", "blue bend", "big rock", "stuart", "day run", "pocahontas", "bishop knob", "cranberry glades"]
FORESTWIDE = ["general prohibitions", "food storage", "wilderness areas", "target shooting", "shooting ranges",
              "controlled substances", "designated recreation sites", "motor vehicle operators", "fire restrictions",
              "fire danger", "burn ban", "forest-wide", "forestwide"]


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def monongahela():
    page = get(URL).decode("utf-8", "ignore")
    alerts = []
    for level, body in re.findall(r'<li class="usa-card[^"]*wfs-alert-flag([^"]*)">(.*?)</li>', page, flags=re.S):
        m = re.search(r'<a href="(/r09/monongahela/alerts/[^"]+)"[^>]*>(.*?)</a>', body, flags=re.S)
        if not m:
            continue
        title = text(m.group(2))
        summary = re.search(r'<div class="usa-card__body">(.*?)</div>', body, flags=re.S)
        start = re.search(r"Alert Start Date:</strong>\s*([^<]+)<", body)
        order = re.search(r"Forest Order:</strong>\s*([^<]+)<", body)
        blob = f"{title} {text(summary.group(1)) if summary else ''}".lower()
        # Every alert on this page is the Monongahela's own (one forest, no
        # other forest's places appear), so only the general rules are
        # "forestwide"; PLACES decides whether a road or area item names a place
        # the app has ("ours") or only a road number ("ours-road").
        scope = ("forestwide" if any(k in title.lower() for k in FORESTWIDE)
                 else "ours" if any(k in blob for k in PLACES) else "ours-road" if re.search(r"\b(road|fr|nfs)\b", blob) else "ours")
        alerts.append({"title": title, "level": level.strip() or "information", "summary": text(summary.group(1)) if summary else "",
                       "url": BASE + m.group(1), "start": start.group(1).strip() if start else None,
                       "order": order.group(1).strip() if order else None, "scope": scope})
    return {"status": "ok" if alerts else "empty", "alerts": alerts}


def main():
    # Published next to alerts.json (run by .github/workflows/alerts.yml).
    # On any failure the previous file stays in place and the step is red but
    # non-blocking (continue-on-error), so the old copy keeps being served.
    res = monongahela()
    res["fetchedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "alerts", "monongahela-alerts.json")
    if res["status"] != "ok":
        print("not published:", res["status"])
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(res, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    print(res["status"], len(res["alerts"]))


if __name__ == "__main__":
    sys.exit(main())
