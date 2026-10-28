#!/usr/bin/env python3
"""Closures and notices for the app's state regions, in the shape of the NPS region alert files.

Writes alerts/regions/<id>.json for vsp, vawma, wvsp and wvwma:
  {"fetchedAt", "region", "source", "nps": {"status", "parks", "alerts": [
     {"id", "park", "title", "category", "description", "url", "updated", "dated"}]}}
The key stays `nps` so the app's parseRegionAlerts reads these files unchanged; `source` names the agency.
`park` is the unit's app id (vsp-douthat), `parks` the units whose page answered this run. `category` uses
the NPS words ("Park Closure", "Caution", "Information"). `updated` is a date (YYYY-MM-DD): the release's
publication date where the agency dates it (`dated: "published"`), otherwise the day this job first saw
the notice, carried forward from the last file (`dated: "first-seen"`).

Sources (all public; robots.txt is read each run and a disallowed page is skipped, with its Crawl-delay kept):
- vsp: Virginia DCR, each state park's page (dcr.virginia.gov/state-parks/<park>); DCR's closures page says
  closures are posted "under Park Alerts" on the park pages, as <div id="alert"><span>...</span></div>.
  The vsp region's state forests are Virginia Department of Forestry land with no DCR page: not covered.
- vawma: Virginia DWR, each WMA's page (dwr.virginia.gov/wma/<wma>/), its "Important Notices for this WMA"
  table (alert_title, alert_description).
- wvsp, wvwma: West Virginia publishes no alerts page for state parks or WMAs. Their only public notices
  are news releases (wvstateparks.com press releases, wvdnr.gov posts), read through the sites' public
  WordPress JSON API: releases from the last RELEASE_DAYS days whose title names one of the region's units
  and a closure word (closed, reopened, restriction, advisory and the like). No release is not "open".

Refresh window: a region whose file is under REFRESH_HOURS old is not refetched. A region where no page
answered keeps its last good alerts, marked "stale". Errors are printed as their type only.
Run by .github/workflows/alerts.yml. Tests: python3 -m unittest test_fetch_state_alerts
"""
import hashlib, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request, urllib.robotparser
from datetime import datetime, timedelta, timezone

UA = "Mossback alerts (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "alerts", "regions")
REFRESH_HOURS = 5.5  # with the 3 hour schedule: every other run
RELEASE_DAYS = 120
PAUSE = 1.5  # seconds between requests to one host, unless its robots.txt asks for more
MAX_TEXT = 600

DCR = "https://www.dcr.virginia.gov/state-parks/"
DWR = "https://dwr.virginia.gov/wma/"
# (app unit id, page slug); the vsp region's state forests have no DCR page.
VSP = [("vsp-bear-creek-lake", "bear-creek-lake"), ("vsp-belle-isle", "belle-isle"), ("vsp-caledon", "caledon"),
       ("vsp-chippokes", "chippokes"), ("vsp-clinch-river", "clinch-river"),
       ("vsp-culpeper-battlefields", "culpeper-battlefields"), ("vsp-douthat", "douthat"),
       ("vsp-fairy-stone", "fairy-stone"), ("vsp-false-cape", "false-cape"), ("vsp-first-landing", "first-landing"),
       ("vsp-hayfields", "hayfields"), ("vsp-high-bridge", "high-bridge-trail"), ("vsp-holliday-lake", "holliday-lake"),
       ("vsp-james-river", "james-river"), ("vsp-kiptopeke", "kiptopeke"), ("vsp-lake-anna", "lake-anna"),
       ("vsp-machicomoco", "machicomoco"), ("vsp-natural-bridge", "natural-bridge"),
       ("vsp-new-river-trail", "new-river-trail"), ("vsp-occoneechee", "occoneechee"), ("vsp-pocahontas", "pocahontas"),
       ("vsp-powhatan", "powhatan"), ("vsp-sailors-creek", "sailors-creek"), ("vsp-seven-bends", "seven-bends"),
       ("vsp-smith-mountain-lake", "smith-mountain-lake"),
       ("vsp-staunton-river-battlefield", "staunton-river-battlefield"), ("vsp-staunton-river", "staunton-river"),
       ("vsp-twin-lakes", "twin-lakes"), ("vsp-westmoreland", "westmoreland"), ("vsp-widewater", "widewater"),
       ("vsp-wilderness-road", "wilderness-road"), ("vsp-york-river", "york-river")]
VAWMA = [(f"vawma-{s}", s) for s in (
    "amelia", "big-survey", "c-f-phelps", "cavalier", "chickahominy", "clinch-mountain", "crooked-creek",
    "fairystone-farms", "featherfin", "gathright", "havens", "hidden-valley", "highland", "horsepen-lake",
    "little-north-mountain", "merrimac-farm", "pettigrew", "powhatan", "ragged-island", "short-hills",
    "smith-mountain-cooperative", "stewarts-creek", "thompson", "turkeycock", "tye-river", "weston",
    "white-oak-mountain")]
# (app unit id, phrases a release title must contain to be about it); bare names like "Panther" are
# not enough because other places share them.
WVSP = [("wvsp-audra", ["audra"]), ("wvsp-cacapon", ["cacapon"]), ("wvsp-cathedral", ["cathedral state park"]),
        ("wvsp-coopers-rock", ["coopers rock", "cooper's rock"]), ("wvsp-holly-river", ["holly river"]),
        ("wvsp-kanawha", ["kanawha state forest"]), ("wvsp-lost-river", ["lost river state park"]),
        ("wvsp-twin-falls", ["twin falls"]), ("wvsp-tygart-lake", ["tygart lake"]),
        ("wvsp-valley-falls", ["valley falls"])]
WVWMA = [(f"wvwma-{s}", [f"{n} wma", f"{n} wildlife management area"]) for s, n in (
    ("allegheny", "allegheny"), ("bear-rock-lakes", "bear rock lakes"), ("beech-fork-lake", "beech fork lake"),
    ("big-ditch", "big ditch"), ("bluestone-lake", "bluestone lake"), ("burnsville-lake", "burnsville lake"),
    ("cheat-canyon", "cheat canyon"), ("dents-run", "dents run"), ("east-lynn-lake", "east lynn lake"),
    ("elk-river", "elk river"), ("frozen-camp", "frozen camp"), ("green-bottom", "green bottom"),
    ("hughes-river", "hughes river"), ("lewis-wetzel", "lewis wetzel"), ("little-canaan", "little canaan"),
    ("mcclintic", "mcclintic"), ("moncove-lake", "moncove lake"), ("nathaniel-mountain", "nathaniel mountain"),
    ("panther", "panther"), ("pedlar", "pedlar"), ("pleasant-creek", "pleasant creek"),
    ("plum-orchard-lake", "plum orchard lake"), ("ritchie-mines", "ritchie mines"),
    ("shannondale-springs", "shannondale springs"), ("short-mountain", "short mountain"),
    ("sideling-hill", "sideling hill"), ("snake-hill", "snake hill"), ("stonecoal-lake", "stonecoal lake"),
    ("stonewall-jackson-lake", "stonewall jackson lake"), ("summersville-lake", "summersville lake"),
    ("teter-creek-lake", "teter creek lake"), ("underwood", "cecil h. underwood"), ("wallback", "wallback"))]
WV_FEEDS = {"wvsp": ["https://wvstateparks.com/wp-json/wp/v2/press-release", "https://wvstateparks.com/wp-json/wp/v2/posts",
                     "https://wvdnr.gov/wp-json/wp/v2/posts"],
            "wvwma": ["https://wvdnr.gov/wp-json/wp/v2/posts"]}
NOTICE_WORDS = re.compile(r"\b(clos(e|ed|es|ing|ure|ures)|re-?open(s|ed|ing)?|restrict(ed|ion|ions)?|advisor(y|ies)|"
                          r"suspend(ed|s)?|cancel(l?ed|s)?|burn ban|boil water|detour|evacuat\w*|warning)\b", re.I)
SOURCES = {"vsp": "Virginia DCR state park pages", "vawma": "Virginia DWR WMA pages",
           "wvsp": "WV State Parks and WVDNR news releases", "wvwma": "WVDNR news releases"}


class Fetcher:
    """GETs that honor robots.txt (Allow/Disallow and Crawl-delay) per host, pause between requests and
    fetch a URL once per run (both WV regions read wvdnr.gov's releases)."""

    def __init__(self, opener=None, sleep=time.sleep):
        self.robots, self.last, self.cache, self.sleep = {}, {}, {}, sleep
        self.open = opener or (lambda url: urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read())

    def _robots(self, host):
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.parse(self.open(f"https://{host}/robots.txt").decode("utf-8", "ignore").splitlines())
            except urllib.error.HTTPError as e:  # as urllib.robotparser: 401/403 forbid all, 404 (DWR) none
                rp.parse(["User-agent: *", "Disallow: /"] if e.code in (401, 403) else [])
            except Exception:  # unreachable: the page itself will fail too, so allow and let it
                rp.parse([])
            self.robots[host] = rp
        return self.robots[host]

    def allowed(self, url):
        return self._robots(urllib.parse.urlsplit(url).netloc).can_fetch(UA, url)

    def get(self, url):
        if url in self.cache:
            return self.cache[url]
        host = urllib.parse.urlsplit(url).netloc
        rp = self._robots(host)
        if not rp.can_fetch(UA, url):
            raise PermissionError("robots.txt disallows")
        wait = max(PAUSE, float(rp.crawl_delay(UA) or 0)) - (time.monotonic() - self.last.get(host, -1e9))
        if wait > 0:
            self.sleep(wait)
        try:
            self.cache[url] = self.open(url).decode("utf-8", "ignore")
            return self.cache[url]
        finally:
            self.last[host] = time.monotonic()


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def category(text):
    """A closure word wins ("closed Nov. 9, reopens Nov. 12" is a closure); a reopening alone is news."""
    t = text.lower()
    if re.search(r"\bclos(e|ed|es|ing|ure|ures)\b", t):
        return "Park Closure"
    if re.search(r"\bre-?open", t):
        return "Information"
    if re.search(r"advisor|warning|caution|boil water|burn ban|restrict|detour|evacuat", t):
        return "Caution"
    return "Information"


ABBREV = re.compile(r"(\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|st|mt|rt|rte|no|a\.m|p\.m)|\b[a-z])\.$", re.I)


def short_title(text, limit=90):
    """The first sentence, not cut at "Nov." or "p.m." (DCR writes "closed Nov. 9-11")."""
    first = ""
    for part in re.split(r"(?<=[.!?])\s+", text):
        first = f"{first} {part}".strip()
        if not ABBREV.search(first):
            break
    return first if len(first) <= limit else first[:limit].rsplit(" ", 1)[0] + "…"


def alert_id(unit, text):
    return hashlib.sha1(f"{unit}|{text.lower()}".encode()).hexdigest()[:16]


def dcr_alerts(page):
    m = re.search(r'<div id="alert">(.*?)</div>', page, re.S)
    return [t for t in (clean(s) for s in re.findall(r"<span[^>]*>(.*?)</span>", m.group(1), re.S))] if m else []


def dwr_alerts(page):
    m = re.search(r'<section class="alerts">(.*?)</section>', page, re.S)
    out = []
    for row in re.findall(r"<td>(.*?)</td>", m.group(1), re.S) if m else []:
        title = clean((re.search(r'class="alert_title">(.*?)</span>', row, re.S) or [None, ""])[1])
        desc = clean((re.search(r'class="alert_description">(.*?)</span>', row, re.S) or [None, ""])[1])
        if title or desc:
            out.append((title, desc))
    return out


def page_region(units, base, parse, fetch):
    """(parks that answered, alerts) for a region read one unit page at a time."""
    parks, alerts = [], []
    for unit, slug in units:
        url = base + slug + ("/" if base == DWR else "")
        try:
            found = parse(fetch.get(url))
        except Exception as e:
            print(f"{unit}: {type(e).__name__}")
            continue
        parks.append(unit)
        for item in found:
            title, desc = item if isinstance(item, tuple) else (short_title(item), item)
            if not (title or desc):
                continue
            body = desc or title
            alerts.append({"id": alert_id(unit, f"{title}|{body}"), "park": unit, "title": title,
                           "category": category(f"{title} {body}"), "description": body[:MAX_TEXT],
                           "url": url, "updated": None, "dated": "first-seen"})
    return parks, alerts


def release_region(units, feeds, fetch, now):
    """(parks, alerts) from news releases: the units count as answered when every feed answered."""
    after = (now - timedelta(days=RELEASE_DAYS)).strftime("%Y-%m-%dT00:00:00")
    alerts, seen, ok = [], set(), True
    for feed in feeds:
        try:
            posts = json.loads(fetch.get(f"{feed}?per_page=100&after={after}&_fields=id,date,link,title,excerpt"))
        except Exception as e:
            print(f"{feed.split('/')[2]}: {type(e).__name__}")
            ok = False
            continue
        for p in posts if isinstance(posts, list) else []:
            title = clean((p.get("title") or {}).get("rendered"))
            if not title or not NOTICE_WORDS.search(title) or p.get("link") in seen:
                continue
            low = title.lower().replace("’", "'")
            for unit, phrases in units:
                if any(ph in low for ph in phrases):
                    seen.add(p.get("link"))
                    alerts.append({"id": alert_id(unit, p.get("link") or title), "park": unit, "title": title,
                                   "category": category(title),
                                   "description": clean((p.get("excerpt") or {}).get("rendered"))[:MAX_TEXT],
                                   "url": p.get("link"), "updated": (p.get("date") or "")[:10] or None,
                                   "dated": "published"})
    return ([u for u, _ in units] if ok else []), alerts


def date_first_seen(alerts, old, today):
    """Pages that do not date their notices get the day this job first saw them, kept from the last file."""
    before = {a.get("id"): a.get("updated") for a in ((old or {}).get("nps") or {}).get("alerts") or []}
    for a in alerts:
        if a["dated"] == "first-seen":
            a["updated"] = before.get(a["id"]) or today
    return alerts


def region_file(region, parks, alerts, old, now):
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if parks:
        nps = {"status": "ok", "parks": parks, "alerts": date_first_seen(alerts, old, now.strftime("%Y-%m-%d"))}
    elif ((old or {}).get("nps") or {}).get("status") in ("ok", "stale"):
        nps = {**old["nps"], "status": "stale", "staleSince": old["nps"].get("staleSince") or stamp}
    else:
        nps = {"status": "error: no page answered", "parks": [], "alerts": []}
    return {"fetchedAt": stamp, "region": region, "source": SOURCES[region], "nps": nps}


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def fresh(old, now, hours=REFRESH_HOURS):
    try:
        at = datetime.fromisoformat(old["fetchedAt"].replace("Z", "+00:00"))
    except (TypeError, KeyError, AttributeError, ValueError):
        return False
    return (now - at).total_seconds() < hours * 3600 and old.get("nps", {}).get("status") == "ok"


def main(argv=None, fetch=None):
    out = OUT if not argv else argv[0]
    now = datetime.now(timezone.utc)
    fetch = fetch or Fetcher()
    jobs = {"vsp": lambda: page_region(VSP, DCR, dcr_alerts, fetch),
            "vawma": lambda: page_region(VAWMA, DWR, dwr_alerts, fetch),
            "wvsp": lambda: release_region(WVSP, WV_FEEDS["wvsp"], fetch, now),
            "wvwma": lambda: release_region(WVWMA, WV_FEEDS["wvwma"], fetch, now)}
    os.makedirs(out, exist_ok=True)
    for region, job in jobs.items():
        path = os.path.join(out, f"{region}.json")
        old = read_json(path)
        if fresh(old, now):
            print(f"{region}: under {REFRESH_HOURS} hours old; not refetched")
            continue
        try:
            parks, alerts = job()
        except Exception as e:
            print(f"{region}: {type(e).__name__}")
            parks, alerts = [], []
        data = region_file(region, parks, alerts, old, now)
        with open(path, "w") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
        print(f"{region}: {data['nps']['status']}, {len(data['nps']['parks'])} units, {len(data['nps']['alerts'])} notices")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
