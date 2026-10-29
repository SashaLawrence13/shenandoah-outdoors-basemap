#!/usr/bin/env python3
"""Live seasonal road closures for the Mossback app (ROAD CLOSURES LANE, build 19).

Reads the alerts this job already fetched, no key and no extra requests:
  alerts/alerts.json            NPS alerts (main parks), GW&J forest alerts, Pisgah's if present
  alerts/regions/<id>.json      every other park's NPS alerts (and the state regions' notices)
  alerts/monongahela-alerts.json  the Monongahela's forest alerts
and the watch list scripts/road_closures_live.json (mirrored from the app repo's
data-pipeline/alerts/road_closures_live.json, written by build_road_closures.py): each seasonal road the
app bundles, with the words an alert would use for it ("Tioga Road", "FR 274", "Crisman Hollow Road").

Writes alerts/road-closures.json:
  {"fetchedAt", "status", "items": [{"id", "status", "title", "text", "url", "updated"}]}
`id` is the app's road record id; `status` is "closed" or "open" (an alert saying the road reopened or
opened for the season); `text` is the sentence that said so; `updated` the alert's own date. A road with no
alert naming it has no item: the app then shows its usual season. Matching is narrow on purpose, as in the
app's routeClosures.ts: one sentence must name the road and say closed or open; night-only gates, lane
closures, delays and "closed to bicycles" style sentences never count.

Atomic write; on any failure the previous file stays and the script exits 1 (the step is continue-on-error).
Run by .github/workflows/alerts.yml after the alert steps. Tests: python3 -m unittest test_fetch_road_closures
"""
import glob, html, json, os, re, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ALERTS = os.path.join(HERE, "..", "alerts")
WATCH = os.path.join(HERE, "road_closures_live.json")
OUT = os.path.join(ALERTS, "road-closures.json")

CLOSED = re.compile(r"\b(closed|closure|closes|closing)\b", re.I)
OPEN = re.compile(r"\b(re-?opened|reopens|reopening|is (now )?open|are (now )?open|now open|has opened|opened|will open|opens)\b", re.I)
NOT_A_CLOSURE = re.compile(
    r"\b(nightly|overnight|at night|dusk|lane closure|one lane|single lane|delays?|closed to (bicycles|bikes|"
    r"pedestrians|hikers|foot traffic|oversize|trailers|rvs|vehicles over)|parking (lot|area) (is )?closed|"
    r"restrooms?|visitor center|campground|picnic area)\b", re.I)


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", clean(s)) if x.strip()]


ROAD_WORD = re.compile(r"\b(road|rd|highway|hwy|drive|pass|route|sr|byway|parkway|entrance|motor nature trail)\b", re.I)


def named(sentence, words):
    """The longest of `words` the sentence names whole, or "" when none."""
    low = sentence.lower()
    hits = [w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", low)]
    return max(hits, key=len) if hits else ""


def verdict(sentence):
    """'closed', 'open' or None for one sentence that already names the road."""
    if NOT_A_CLOSURE.search(sentence):
        return None
    opened = OPEN.search(sentence)
    closed = CLOSED.search(sentence)
    if opened and not closed:
        return "open"
    if closed and opened:
        # "reopened after the closure" / "closed ... will reopen in May": the first verb wins.
        return "open" if opened.start() < closed.start() else "closed"
    return "closed" if closed else None


def alerts_from(path, keys):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for k in keys:
        node = data
        for part in k.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        out += [a for a in (node or []) if isinstance(a, dict)]
    return out


def gather(alerts_dir=ALERTS):
    """Every NPS and forest alert the job wrote this run, as (kind, park or None, alert)."""
    found = []
    main = os.path.join(alerts_dir, "alerts.json")
    for a in alerts_from(main, ["nps.alerts"]):
        found.append(("nps", a.get("park"), a))
    for path in sorted(glob.glob(os.path.join(alerts_dir, "regions", "*.json"))):
        for a in alerts_from(path, ["nps.alerts"]):
            found.append(("nps", a.get("park"), a))
    for a in alerts_from(main, ["forest.alerts", "pisgah.alerts", "monongahela.alerts"]):
        found.append(("forest", None, a))
    for a in alerts_from(os.path.join(alerts_dir, "monongahela-alerts.json"), ["alerts"]):
        found.append(("forest", None, a))
    seen, out = set(), []
    for kind, park, a in found:
        key = (a.get("id") or a.get("url"), a.get("title"))
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, park, a))
    return out


def match(watch, alerts):
    """One item per road at most. A sentence goes to the road(s) it names most exactly: "North Rim Scenic
    Road Closure: Cape Royal and Point Imperial" is the Cape Royal road's, not all of the North Rim's. A
    sentence must also say road, drive, highway, pass and the like, unless the matched words do."""
    best = {}
    for kind, park, a in alerts:
        mine = [r for r in watch if (r.get("nps") == park if kind == "nps" else kind == "forest" and not r.get("nps"))]
        if not mine:
            continue
        title = clean(a.get("title"))
        body = a.get("description") or a.get("summary") or ""
        done = set()
        for s in [title] + sentences(body):
            hits = [(named(s, r["words"]), r) for r in mine]
            hits = [(w, r) for w, r in hits if w and (ROAD_WORD.search(s))]
            if not hits:
                continue
            longest = max(len(w) for w, _ in hits)
            v = verdict(s)
            if not v:
                continue
            for w, r in hits:
                if len(w) != longest or r["id"] in done:
                    continue
                done.add(r["id"])
                cand = {"id": r["id"], "status": v, "title": title, "text": s[:300],
                        "url": a.get("url"), "updated": a.get("updated") or a.get("start")}
                # A closure outranks an opening when two alerts disagree.
                old = best.get(r["id"])
                if old is None or (v == "closed" and old["status"] == "open"):
                    best[r["id"]] = cand
    return [best[r["id"]] for r in watch if r["id"] in best]


def main():
    try:
        with open(WATCH) as f:
            watch = json.load(f)["roads"]
        items = match(watch, gather())
        out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "ok",
               "roads": len(watch), "items": items}
        os.makedirs(ALERTS, exist_ok=True)
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, OUT)
        print(f"road closures: {len(items)} live items for {len(watch)} roads")
    except Exception as e:  # noqa: BLE001
        print(f"road closures failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
