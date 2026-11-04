#!/usr/bin/env python3
"""Daniel Boone NF alerts (DANIEL BOONE LANE, 2026-10-30): the forest's Alerts & Notices page parsed once a day, in the shape of
fetch_alerts_pisgah.py. No feed exists (research/alerts-source.md: no RSS, Atom, JSON or Drupal REST; RIDB notices are standing text),
so the HTML list is the source: one page, no paging, each alert a `li.wfs-alert-flag` card with a level class, a title link (the slug is
the stable key), a blurb, "Alert Start Date" and sometimes "Forest Order". The site banner's "Fire Danger Status" is read too.

Each alert gets a scope: "ours" (names a place the app has), "forestwide" (a general rule or a standing 2015 Forest Order) or "ours-road"
(a road item naming no place), and `stale` when it started over a year ago (the list shows no end date; a stale item reads "check with the
ranger district", never as current). The legend cards at the top of the list (Critical, Caution...) carry no alert link and are skipped.

NOT WIRED: the daily run belongs in the basemap repo's alerts job, which uses GitHub Actions minutes, paused until 11-02 (owner rule).
Wire after 11-02. Run standalone it prints the alerts; `--out FILE` saves the JSON. Test: python3 -m unittest test_fetch_alerts_danielboone
"""
import html
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone

UA = "Mozilla/5.0 (Macintosh) data-pipeline"
URL = "https://www.fs.usda.gov/r08/danielboone/alerts"
BASE = "https://www.fs.usda.gov"
PLACES = ["red river gorge", "gorge", "clifty", "sky bridge", "natural bridge", "nada tunnel", "gladie", "koomer", "tunnel ridge", "chimney top",
          "auxier", "gray's arch", "grays arch", "rock bridge", "swift camp", "suspension bridge", "cave run", "zilpo", "twin knobs", "tater knob",
          "pioneer weapons", "white sulphur", "laurel river", "rockcastle", "sinking creek", "hawk creek", "bee rock", "s-tree", "turkey foot",
          "cane creek", "vanhook", "london", "pulaski", "laurel county", "stearns", "yahoo", "natural arch", "beaver creek", "dog slaughter",
          "cumberland falls", "cliffside", "barren fork", "redbird", "big creek", "sheltowee", "scott creek", "grove", "holly bay", "craigs creek",
          "clear creek", "morehead", "winchester", "tornado"]
FORESTWIDE = ["prohibition", "general prohibitions", "food storage", "wilderness", "target shooting", "shooting", "controlled substances",
              "developed recreation", "fire restriction", "fire danger", "burn ban", "forest-wide", "forestwide", "camping restrictions",
              "climbing and rappelling", "visitor use monitoring", "non-motorized fishing"]
STALE_DAYS = 365


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%B %d, %Y").date()
    except (ValueError, AttributeError):
        return None


def fire_danger(page):
    """The banner's "Fire Danger Status" word (Low, Moderate, High, Very High, Extreme), or None."""
    # The phrase also labels the banner's button ("Alerts and Fire Danger Status"), so try every place it appears.
    for mm in re.finditer(r"Fire Danger Status", page):
        seg = text(re.sub(r"<svg.*?</svg>", "", page[mm.start():mm.start() + 4000], flags=re.S))
        m = re.match(r"Fire Danger Status\s+(Very High|Low|Moderate|High|Extreme)\b", seg)
        if m:
            return m.group(1)
    return None


def parse(page, today=None):
    today = today or date.today()
    alerts, seen = [], set()
    for level, body in re.findall(r'<li class="usa-card[^"]*wfs-alert-flag([^"]*)">(.*?)</li>', page, flags=re.S):
        m = re.search(r'<a href="(/r08/danielboone/alerts/([^"]+))"[^>]*>(.*?)</a>', body, flags=re.S)
        if not m:
            continue  # the legend cards (Critical, Caution, Information, Fire Restriction) have no alert link
        slug = m.group(2)
        if slug in seen:
            continue
        seen.add(slug)
        title = text(m.group(3))
        summary = re.search(r'<div class="usa-card__body">(.*?)</div>', body, flags=re.S)
        summ = text(summary.group(1)) if summary else ""
        start = re.search(r"Alert Start Date:\s*</strong>\s*([^<]+)<", body)
        order = re.search(r"Forest Order:\s*</strong>\s*([^<]+)<", body)
        start_s = start.group(1).strip() if start else None
        d = parse_date(start_s) if start_s else None
        blob = f"{title} {summ}".lower()
        scope = ("forestwide" if any(k in title.lower() for k in FORESTWIDE)
                 else "ours" if any(k in blob for k in PLACES) else "ours-road" if re.search(r"\b(road|fsr|fr|nfs|ky \d+|hwy)\b", blob) else "ours")
        alerts.append({"slug": slug, "title": title, "level": level.strip() or "information", "summary": summ, "url": BASE + m.group(1),
                       "start": d.isoformat() if d else start_s, "order": order.group(1).strip() if order else None, "scope": scope,
                       "stale": bool(d and (today - d).days > STALE_DAYS)})
    return {"status": "ok" if alerts else "empty", "fireDanger": fire_danger(page), "alerts": alerts}


def main():
    res = parse(get(URL).decode("utf-8", "ignore"))
    res["fetchedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    res["source"] = URL
    if "--out" in sys.argv:
        json.dump(res, open(sys.argv[sys.argv.index("--out") + 1], "w"), indent=1, ensure_ascii=False)
    print(res["status"], len(res["alerts"]), "fire danger:", res["fireDanger"])
    for a in res["alerts"]:
        print(" -", a["scope"], "|", a["level"], "|", a["title"], "|", a["start"], "| stale" if a["stale"] else "")


if __name__ == "__main__":
    sys.exit(main())
