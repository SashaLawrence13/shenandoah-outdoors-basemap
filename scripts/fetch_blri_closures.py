#!/usr/bin/env python3
"""Blue Ridge Parkway road, facility and trail closures for the Mossback app.

The NPS Data API's alerts feed carries nothing for the Parkway most days
(checked 2026-10-05); the real closures are posted by milepost on one HTML
page: https://www.nps.gov/blri/planyourvisit/roadclosures.htm
That page has
  - two tables (Virginia and North Carolina sections: milepost range,
    crossroads, status, "important information") with a "Road status as of"
    heading,
  - "Facility Closures" and "Trail and Backcountry Closures" bullet lists,
  - "Last updated" at the bottom.
This script fetches it, parses it with the standard library only, and writes
alerts/blri-closures.json (a separate file from alerts.json, so a parse
failure here cannot touch the NPS or forest alerts).

Each item keeps the shape of an NPS alert in alerts.json (id, park, title,
category, description, url, updated) so the app can merge them into
feed.nps.alerts, plus: kind, status, mileStart, mileEnd, state, text,
detour, place, asOf, fetchedAt, mileSource.

Robustness: if the page can't be fetched, or no longer looks like the page
this parser knows (no milepost table and no closure headings), nothing is
written and the previous file stays; the exit code is 1 so the workflow step
shows red (it is meant to run with continue-on-error). A page with no
closures at all is a valid page: it yields an empty items list.

Run by the basemap repo's .github/workflows/alerts.yml (see README.md,
"BLRI closures job"). Usage:
  fetch_blri_closures.py [--html saved.htm] [--out file.json] [--raw-dir dir]
"""
import hashlib, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

UA = "Mossback alerts (github.com/SashaLawrence13/shenandoah-outdoors-basemap)"
URL = "https://www.nps.gov/blri/planyourvisit/roadclosures.htm"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "alerts", "blri-closures.json")
VA_MAX_MILE = 216.9  # the state line, as the page's own table gives it
MAX_MILE = 470.0  # the Parkway ends at MP 469.1

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}


# ---------------------------------------------------------------- HTML

class Blocks(HTMLParser):
    """Flattens a page to ordered blocks: ("h2", text), ("p", text), ("li", text),
    ("table", rows). Line breaks inside a block become newlines."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.skip = 0
        self.buf = None  # (tag, [text])
        self.table = None  # list of rows while inside a table
        self.row = None
        self.cell = None
        self.tstack = []  # per open <table>: True for layout tables (CMS wrappers), which are transparent

    def _flush(self):
        if self.buf:
            tag, parts = self.buf
            text = clean("".join(parts))
            if text:
                self.blocks.append((tag, text))
        self.buf = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self.skip += 1
        if self.skip:
            return
        if tag == "table":
            a = dict(attrs)
            layout = self.table is None and (
                "layout" in (a.get("class") or "").lower() or a.get("role") == "presentation")
            self.tstack.append(layout)
            if not layout and self.table is None:
                self._flush()
                self.table = []
        elif self.table is not None:
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = []
            elif tag == "br" and self.cell is not None:
                self.cell.append("\n")
        elif tag in ("h1", "h2", "h3", "h4", "p", "li"):
            self._flush()
            self.buf = (tag, [])
        elif tag == "br" and self.buf:
            self.buf[1].append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "table":
            layout = self.tstack.pop() if self.tstack else True
            if not layout and self.table is not None:
                self.blocks.append(("table", self.table))
                self.table = self.row = self.cell = None
        elif self.table is not None:
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append(clean("".join(self.cell)))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.table.append(self.row)
                self.row = None
        elif tag in ("h1", "h2", "h3", "h4", "p", "li"):
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        data = data.replace("\n", " ")  # source line breaks are not breaks; <br> is
        if self.table is not None:
            if self.cell is not None:
                self.cell.append(data)
        elif self.buf:
            self.buf[1].append(data)

    def close(self):
        super().close()
        self._flush()


def clean(s):
    """Collapse whitespace per line; keep line breaks as single spaces' worth of newline."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.replace("\r", "").split("\n")]
    return "\n".join(ln for ln in lines if ln)


def flat(s):
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- miles

NUM = r"(\d{1,3}(?:\.\d+)?)"
PFX = r"\b(?:M\.?P\.?|mile\s*posts?|miles?)\b\.?\s*"
RANGE_RE = re.compile(rf"{PFX}{NUM}\s*(?:-|–|—|\bto\b|\bthrough\b|\bthru\b|\band\b)\s*(?:{PFX})?{NUM}", re.I)
POINT_RE = re.compile(rf"{PFX}{NUM}", re.I)


def _ok(*nums):
    return all(0 <= n <= MAX_MILE for n in nums)


def parse_miles(text):
    """First milepost or range in free text: (start, end) or (None, None).
    Handles "MP 63.5 to 63.9", "MP 63.5 - MP 63.9", "Milepost 115", "from milepost 115
    to milepost 120", "MP 115-120", "between MP 261.2 and MP 274.3"."""
    for m in RANGE_RE.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        if _ok(a, b):
            return (min(a, b), max(a, b))
    for m in POINT_RE.finditer(text):
        a = float(m.group(1))
        if _ok(a):
            return (a, a)
    return (None, None)


def parse_mile_cell(cell):
    """A table's milepost cell: "0.0 - 2.2", "45.5 -61.4", "202.1 - 216.9 (State Line)", "115.0"."""
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:-|–|—|\bto\b)\s*(\d{1,3}(?:\.\d+)?)", cell)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return (min(a, b), max(a, b)) if _ok(a, b) else (None, None)
    m = re.search(r"\d{1,3}(?:\.\d+)?", cell)
    if m and _ok(float(m.group(0))):
        return (float(m.group(0)), float(m.group(0)))
    return (None, None)


def state_of(text, start=None, end=None):
    """Explicit state words win; otherwise the milepost (VA up to the state line)."""
    va = re.search(r"\b(VA|Virginia)\b", text)
    nc = re.search(r"\b(NC|North Carolina)\b", text)
    if bool(va) != bool(nc):
        return "VA" if va else "NC"
    if start is None:
        return None
    if end is not None and start <= VA_MAX_MILE < end:
        return None  # spans the state line
    return "VA" if start <= VA_MAX_MILE else "NC"


# ---------------------------------------------------------------- dates

def parse_as_of(blocks):
    """(iso, text) from "Road status as of 8:47 A.M, Monday, October 5, 2026." or
    "Last updated: October 5, 2026"."""
    for _, text in ((b[0], b[1]) for b in blocks if isinstance(b[1], str)):
        t = flat(text)
        m = re.search(r"as of\s+(\d{1,2}):(\d{2})\s*([AaPp])\.?\s*[Mm]\.?,?\s+(?:[A-Za-z]+day,?\s+)?([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", t)
        if m and m.group(4).lower() in MONTHS:
            h, mi = int(m.group(1)) % 12, int(m.group(2))
            if m.group(3).lower() == "p":
                h += 12
            y, mo, d = int(m.group(6)), MONTHS[m.group(4).lower()], int(m.group(5))
            iso = f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:00"
            try:
                from zoneinfo import ZoneInfo
                off = datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/New_York")).strftime("%z")
                iso += off[:3] + ":" + off[3:]
            except Exception:  # no tz database: leave it as local park time
                pass
            return iso, m.group(0)
    for _, text in ((b[0], b[1]) for b in blocks if isinstance(b[1], str)):
        t = flat(text)
        m = re.search(r"Last updated:?\s*([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", t)
        if m and m.group(1).lower() in MONTHS:
            return f"{int(m.group(3)):04d}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}", m.group(0)
    return None, None


# ---------------------------------------------------------------- classify

CLOSED_RE = re.compile(r"\bclosed\b|\bclosure\b|\bclosing\b", re.I)
RESTRICT_RE = re.compile(r"single[- ]lane|traffic control|sporadic|\bdelays?\b|one[- ]way|flagg|lane closure|reduced|limited", re.I)
FACILITY_RE = re.compile(
    r"campground|picnic area|visitor center|restaurant|lodge|cabins?|\bstore\b|\binn\b|museum|campsite|amphitheat|"
    r"gift shop|\bcenter\b|waterfall", re.I)
DETOUR_RE = re.compile(r"detour", re.I)


def sentences(text):
    """Sentences, also split at <br> line breaks (kept as newlines by Blocks)."""
    out = []
    for line in text.split("\n"):
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", flat(line)) if s.strip()]
    return out


NOT_A_CLOSURE = re.compile(r"closure information|^check with|^please|^visit|^see ", re.I)


def row_status(status):
    low = status.lower()
    if not low.strip():
        return "unknown"
    if "partial" in low:
        return "restricted"
    if re.search(r"\bclosed\b", low):
        return "closed"
    if re.search(r"single[- ]lane|restricted|limited|delay", low):
        return "restricted"
    if "open" in low or "ungated" in low:
        return "open"
    return "unknown"


def mile_label(a, b):
    def f(x):
        return f"{x:g}"
    return f"MP {f(a)}" if a == b else f"MP {f(a)} to {f(b)}"


def item_id(kind, title, a, b):
    key = f"{kind}|{re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()}|{a}|{b}"
    return "blri-" + hashlib.sha1(key.encode()).hexdigest()[:12]


CATEGORY = {"closed": "Park Closure", "restricted": "Caution", "open-seasonal": "Information", "notice": "Information"}


def make_item(kind, status, title, text, a, b, state, place=None, detour=None, mile_source=None,
              permanent=False):
    return {
        "id": item_id(kind, title, a, b), "park": "blri", "title": title, "category": CATEGORY.get(status, "Information"),
        "description": text, "url": URL, "updated": None,
        "kind": kind, "status": status, "mileStart": a, "mileEnd": b, "state": state, "text": text,
        "detour": detour, "place": place, "mileSource": mile_source, "permanent": permanent,
    }


def row_items(cells, a, b, tbl_state):
    """Items for one table row (cells = mile, crossroads, status, notes)."""
    cross, status, notes = flat(cells[1]), flat(cells[2]), cells[3] if len(cells) > 3 else ""
    st_kind = row_status(status)
    state = tbl_state or state_of(" ".join(cells[:2]), a, b)
    out, used = [], False
    sents = [x for x in sentences(notes) if not NOT_A_CLOSURE.search(x)]
    detours = [x for x in sents if DETOUR_RE.search(x)]
    plain = [x for x in sents if not DETOUR_RE.search(x)]
    # A closed/restricted row whose notes name no mileposts or facility of their own: the row is the item.
    specific = any(parse_miles(x)[0] is not None or FACILITY_RE.search(x) for x in plain)
    for s in ([] if st_kind in ("closed", "restricted") and not specific else plain):
        restrict = bool(RESTRICT_RE.search(s))
        closed = bool(CLOSED_RE.search(s)) and not re.search(r"lane closures?", s, re.I)
        if not (restrict or closed):
            continue
        used = True
        sa, sb = parse_miles(s)
        src = "text" if sa is not None else "row"
        sa, sb = (sa, sb) if sa is not None else (a, b)
        if closed and FACILITY_RE.search(s) and src == "row":
            name = re.split(r"\s+(?:is|are|has been)?\s*CLOSED\b", s, flags=re.I)[0].strip(" -:")
            out.append(make_item("facility", "closed", f"{name} closed", s, sa, sb, state, cross, None, src))
        elif closed:
            title = f"Parkway closed, {mile_label(sa, sb)}"
            out.append(make_item("road", "closed", title, s, sa, sb, state_of(s, sa, sb) if not tbl_state else tbl_state,
                                 cross, " ".join(detours) or None, src))
        else:
            out.append(make_item("road", "restricted", f"Parkway restricted, {mile_label(sa, sb)}", s, sa, sb,
                                 state, cross, None, src))
    if not used and st_kind in ("closed", "restricted"):
        quote = ". ".join(x for x in (f"{cross} (status: {status})", flat(". ".join(sents))) if x)
        if a == b:
            title = f"{cross} {'closed' if st_kind == 'closed' else 'restricted'}"
        else:
            title = f"Parkway {st_kind}, {mile_label(a, b)}"
        out.append(make_item("road", st_kind, title, quote, a, b, state, cross, " ".join(detours) or None, "row"))
    return out


def list_items(heading, text):
    """A bullet under a closures/seasons heading."""
    t = flat(text)
    h = heading.lower()
    kind = "facility" if "facilit" in h else "trail" if "trail" in h or "backcountry" in h else "other"
    seasonal = bool(re.search(r"season|winter|opening", h))
    permanent = bool(re.search(r"permanent", t, re.I))
    if CLOSED_RE.search(t):
        status = "closed"
    elif re.search(r"\b(limited|restricted|reduced)\b", t, re.I):
        status = "restricted"
    elif seasonal and re.search(r"\bopen(s|ed|ing)?\b", t, re.I):
        status = "open-seasonal"
    else:
        return None
    a, b = parse_miles(t)
    m = re.match(r"^(.*?)(?:\s*\(|\s+(?:is|are|has been|have been|will be|remains?)\b|\s+(?:closed|open))", t)
    name = re.sub(r"\s+in\s+(?:VA|NC|Virginia|North Carolina)$", "", (m.group(1) if m else t).strip(" -:,."))
    verb = {"closed": "permanently closed" if permanent else "closed", "restricted": "restricted",
            "open-seasonal": "seasonal"}[status]
    return make_item(kind, status, f"{name} {verb}", t, a, b, state_of(t, a, b),
                     name, None, "text" if a is not None else None, permanent)


def parse_closures(html_text, fetched_at, url=URL):
    """The parsed page, or None if it doesn't look like the Parkway's road status page."""
    p = Blocks()
    p.feed(html_text)
    p.close()
    blocks = p.blocks
    as_of, as_of_text = parse_as_of(blocks)
    items, sections, heading, last_p = [], [], "", ""
    seen_table = False
    closure_heading = False
    for kind, val in blocks:
        if kind in ("h1", "h2", "h3", "h4"):
            heading = val
            if re.search(r"closure|road status|season|winter", val, re.I):
                closure_heading = True
        elif kind == "p":
            last_p = val
            if re.search(r"winter|seasonal", heading, re.I) and (CLOSED_RE.search(val) or re.search(r"\bopen", val, re.I)) \
               and len(val) < 500 and not re.search(r"^please|for additional|^see ", val, re.I):
                it = list_items(heading, val)
                if it:
                    items.append(it)
        elif kind == "table":
            rows = val
            if not any(re.search(r"mile\s*post", " ".join(r), re.I) for r in rows[:1]):
                continue
            ctx = flat(last_p).lower()
            tbl_state = "VA" if "virginia" in ctx else "NC" if "north carolina" in ctx else None
            for cells in rows[1:]:
                if len(cells) < 3:
                    continue
                a, b = parse_mile_cell(cells[0])
                if a is None:
                    continue
                seen_table = True
                cells = cells + [""] * (4 - len(cells))
                st = tbl_state or state_of(cells[0] + " " + cells[1], a, b)
                sections.append({"mileStart": a, "mileEnd": b, "state": st, "crossroads": flat(cells[1]),
                                 "status": flat(cells[2]), "statusKind": row_status(cells[2]),
                                 "notes": flat(cells[3])})
                items.extend(row_items(cells, a, b, tbl_state))
        elif kind == "li":
            if re.search(r"closure|facilit|trail|backcountry|season|winter|opening", heading, re.I):
                it = list_items(heading, val)
                if it:
                    items.append(it)
    if not seen_table and not closure_heading:
        return None  # not the page this parser knows
    # De-duplicate (the same note repeats on several table rows), keep page order.
    uniq, ids = [], set()
    for it in items:
        key = (it["id"], it["status"], it["text"])
        if key in ids:
            continue
        ids.add(key)
        it["updated"] = as_of
        it["asOf"] = as_of
        it["fetchedAt"] = fetched_at
        uniq.append(it)
    # Two different texts with the same id (same title and miles) must not collide.
    count = {}
    for it in uniq:
        n = count.get(it["id"], 0)
        count[it["id"]] = n + 1
        if n:
            it["id"] = f"{it['id']}-{n + 1}"
    return {"fetchedAt": fetched_at, "source": url, "status": "ok", "asOf": as_of, "asOfText": as_of_text,
            "items": uniq, "sections": sections}


# ---------------------------------------------------------------- main

def get(url, tries=3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        except Exception as e:  # retry transient failures
            err = e
            time.sleep(2 * (i + 1))
    raise err


def main(argv):
    args = dict(zip(argv[::2], argv[1::2]))
    out = args.get("--out", OUT)
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        if "--html" in args:
            with open(args["--html"], encoding="utf-8") as fh:
                html_text = fh.read()
        else:
            html_text = get(URL)
    except Exception as e:
        print(f"blri closures: fetch failed ({type(e).__name__}); keeping the previous file", file=sys.stderr)
        return 1
    if args.get("--raw-dir"):
        os.makedirs(args["--raw-dir"], exist_ok=True)
        with open(os.path.join(args["--raw-dir"], "roadclosures.htm"), "w", encoding="utf-8") as fh:
            fh.write(html_text)
    try:
        result = parse_closures(html_text, fetched)
    except Exception as e:
        print(f"blri closures: parse error ({type(e).__name__}: {e}); keeping the previous file", file=sys.stderr)
        return 1
    if result is None:
        print("blri closures: page layout not recognised; keeping the previous file", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    os.replace(tmp, out)  # atomic: a crash never leaves half a file
    print({"items": len(result["items"]), "sections": len(result["sections"]), "asOf": result["asOf"]})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
