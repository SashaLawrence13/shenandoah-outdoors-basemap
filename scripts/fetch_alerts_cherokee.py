#!/usr/bin/env python3
"""Cherokee NF alerts (2026-11-04): the forest's Alerts & Notices page parsed once a day, in the shape of fetch_alerts_danielboone.py
(and fetch_alerts_pisgah.py before it). The Cherokee's page, fs.usda.gov/r08/cherokee/alerts, has the same Region 8 template as the
Daniel Boone's, Pisgah's and the GW&J's: one page, no paging, each alert a `li.wfs-alert-flag` card with a level class (critical, caution,
information, fire-restriction), a title link (the slug is the stable key), a blurb, "Alert Start Date" and sometimes "Forest Order". No feed
exists, so the HTML list is the source. The site banner's "Fire Danger Status" is read too.

Each alert gets a scope: "ours" (names a place the app has), "forestwide" (a general rule or standing order with no place in its title),
or "ours-road" (a road item naming no place), and `stale` when it started over a year ago (the list shows no end date; a stale item reads
"check with the ranger district", never as current). A place named in the TITLE wins over a forest-wide keyword, so "Roan Mountain Fire
Restrictions" stays a Roan item. The legend cards at the top of the list (Critical, Caution...) carry no alert link and are skipped.

NOT WIRED INTO A JOB: the daily run belongs in the basemap repo's alerts job (the lead moves this scraper there). Until that job
publishes a `cherokee` key, the app's card says the alerts are not connected, never "no alerts". Run standalone it prints the alerts;
`--out FILE` saves the JSON. Test: python3 -m unittest test_fetch_alerts_cherokee
"""
import html
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone

UA = "Mozilla/5.0 (Macintosh) data-pipeline"
URL = "https://www.fs.usda.gov/r08/cherokee/alerts"
BASE = "https://www.fs.usda.gov"
PLACES = ["tellico", "bald river", "citico", "indian boundary", "ocoee", "parksville", "hiwassee", "big frog", "unaka", "rock creek",
          "erwin", "beauty spot", "roan", "watauga", "laurel fork", "dennis cove", "shady valley", "backbone rock", "cherohala", "skyway",
          "hurricane gap", "dick creek", "paint creek", "horse creek", "warden field", "donley", "spring creek", "thunder rock", "gassaway",
          "peavine", "sheeds", "milligan", "mckamy", "allen branch", "paint rock", "appalachian trail", "unicoi", "nolichucky", "doe river",
          "iron mountain", "holston", "pond mountain", "big laurel", "flint mill", "rocky fork", "cohutta", "benton mackaye", "hwy 395",
          "carter county", "johnson county", "polk county", "monroe county", "greene county", "unicoi county", "washington county"]
FORESTWIDE = ["campsite limitations", "camping restrictions", "wilderness closure", "wilderness areas", "general prohibitions", "prohibition",
              "food storage", "target shooting", "shooting", "controlled substances", "developed recreation", "fire restriction",
              "fire danger", "burn ban", "forest-wide", "forestwide", "motorboats", "forest service lakes", "visitor use monitoring"]
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


def scope_of(title, summ):
    t, blob = title.lower(), f"{title} {summ}".lower()
    if any(k in t for k in PLACES):
        return "ours"
    if any(k in t for k in FORESTWIDE):
        return "forestwide"
    if any(k in blob for k in PLACES):
        return "ours"
    return "ours-road" if re.search(r"\b(road|fsr|fr|nfs|nfsr|hwy)\b", blob) else "ours"


def parse(page, today=None):
    today = today or date.today()
    alerts, seen = [], set()
    for level, body in re.findall(r'<li class="usa-card[^"]*wfs-alert-flag([^"]*)">(.*?)</li>', page, flags=re.S):
        m = re.search(r'<a href="(/r08/cherokee/alerts/([^"]+))"[^>]*>(.*?)</a>', body, flags=re.S)
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
        alerts.append({"slug": slug, "title": title, "level": level.strip() or "information", "summary": summ, "url": BASE + m.group(1),
                       "start": d.isoformat() if d else start_s, "order": order.group(1).strip() if order else None,
                       "scope": scope_of(title, summ), "stale": bool(d and (today - d).days > STALE_DAYS)})
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
