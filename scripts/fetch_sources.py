#!/usr/bin/env python3
"""Fetch the Signal Flow Daily source pages and save what came back.

Writes data/sources.json: for every source, the HTTP status, any error and the
first part of the page text (scripts/styles/tags stripped). The daily Claude
task reads this file instead of fetching the pages itself, so a failed fetch is
recorded here as a failure, never silently as "nothing new".
Standard library only.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 SignalFlowDailyFetcher")
TEXT_LIMIT = 14000  # newest-first lists: the top of the page is what matters

BARCO = ("https://www.barco.com/en/news/news-insights?productCategory="
         "barco-dxp:product/product-category/projection,"
         "barco-dxp:product/product-category/image-processing,"
         "barco-dxp:product/product-category/video-walls")

SOURCES = [
    ("panasonic_whatsnew", "https://pro-av.panasonic.net/en/whatsnew/", 14000),
    ("smpte_st2110", "https://www.smpte.org/blog/tag/st-2110", 8000),
    ("aws_media_services", "https://aws.amazon.com/blogs/aws/category/media-services/", 8000),
    ("analogway_news", "https://www.analogway.com/news-center", 10000),
    ("novastar_news", "https://www.novastar.tech/content/content.html?catid=19", 6000),
    ("pixelhue_home", "https://pixelhue.com", 8000),
    ("barco_portal", BARCO, 10000),
    ("barco_live_events", "https://www.barco.com/en/news/news-insights/live-events", 10000),
    ("barco_software", "https://www.barco.com/en/support/software/", 8000),
    ("blackmagic_media", "https://www.blackmagicdesign.com/media/", 6000),
    ("blackmagic_archive", "https://www.blackmagicdesign.com/media/release/archive", 6000),
    ("blackmagic_release_notes", "https://www.blackmagicdesign.com/support/partial/llm-release-notes-summary", 40000),
    ("evolve_millumin", "https://evolveacademy.live/product/millumin/", 6000),
    ("evolve_pixera", "https://evolveacademy.live/product/pixera/", 6000),
    ("evolve_livepremier_l1", "https://evolveacademy.live/product/live-premier-level-1/", 6000),
    ("evolve_encore3", "https://evolveacademy.live/product/encore3-certified-specialist/", 6000),
    ("evolve_pixelhue_l2", "https://evolveacademy.live/product/pixelhue-level-2-advanced-certification/", 6000),
    ("evolve_pixelhue_remote", "https://evolveacademy.live/product/pixelhue-remote-training/", 6000),
    ("evolve_view_all", "https://evolveacademy.live/view-all/", 8000),
    # Trade shows (dates are re-checked every run; shows are listed on the "Coming up" grid)
    ("show_ldi", "https://www.ldishow.com/", 5000),
    ("show_ise", "https://www.iseurope.org/", 5000),
    ("show_nab", "https://www.nabshow.com/", 6000),
    ("show_nab_why", "https://www.nabshow.com/exhibit-sponsor/las-vegas/why-nab-show/", 4000),
    ("show_infocomm", "https://www.infocommshow.org/", 6000),
    ("show_ibc", "https://show.ibc.org/register-interest-ibc2027", 4000),
    ("show_ibc_home", "https://show.ibc.org/", 6000),
    ("class_millumin_nyc", "https://millumin.com/nyc/", 6000),
    ("analogway_academy_sessions", "https://academy.analogway.com/in-person-trainings/training-sessions", 30000),
]


def strip_html(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|br|section|article|ul|ol|table)>|<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    lines = [re.sub(r"[ \t\r\f\v ]+", " ", l).strip() for l in raw.split("\n")]
    return "\n".join(l for l in lines if l)


def fetch(url, tries=4):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read(3_000_000)
                try:
                    enc = r.headers.get_content_charset() or "utf-8"
                    txt = body.decode(enc, errors="replace")
                except (LookupError, ValueError):  # e.g. charset header "none"
                    txt = body.decode("utf-8", errors="replace")
                return {"status": r.status, "error": None, "text": txt}
        except urllib.error.HTTPError as e:
            last = {"status": e.code, "error": f"HTTP {e.code}", "text": ""}
            if e.code in (403, 404, 410):
                break
            if e.code == 429:  # rate limited: one patient retry, then give up
                if n >= 1:
                    break
                time.sleep(45)
                continue
        except Exception as e:  # noqa: BLE001
            last = {"status": None, "error": f"{type(e).__name__}: {e}", "text": ""}
        time.sleep(3 * (n + 1))
    return last


def collect(sid, url, limit):
    res = fetch(url)
    text = strip_html(res["text"]) if res["text"] else ""
    ok = res["status"] == 200 and len(text) > 200
    return {"url": url, "ok": ok, "http_status": res["status"], "error": res["error"],
            "newest_date_seen": newest_date_seen(text[:limit], dt.date.today()),
            "chars_total": len(text), "truncated": len(text) > limit,
            "text": text[:limit]}


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RES = [
    (re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b([A-Z][a-z]{2,8})\.? (\d{1,2}),? (20\d\d)\b"),
     lambda m: (int(m[3]), MONTHS.get(m[1][:3].lower(), 0), int(m[2]))),
    (re.compile(r"\b(\d{1,2}) ([A-Za-z]{3,9})\.?,? (20\d\d)\b"),
     lambda m: (int(m[3]), MONTHS.get(m[2][:3].lower(), 0), int(m[1]))),
]


def newest_date_seen(text, today):
    """Heuristic: the latest plausible date written anywhere in the text."""
    best = None
    for rx, conv in DATE_RES:
        for m in rx.finditer(text):
            try:
                y, mo, d = conv(m)
                dd = dt.date(y, mo, d)
            except (ValueError, TypeError):
                continue
            if dd <= today and (best is None or dd > best):
                best = dd
    return best.isoformat() if best else None


# Trade news feeds. Each site lists candidate feed URLs; the first that parses wins.
TRADE_FEEDS = {
    "sportsvideo.org": ["https://www.sportsvideo.org/feed/"],
    "svgeurope.org": ["https://www.svgeurope.org/feed/"],
    "tvtechnology.com": ["https://www.tvtechnology.com/feeds.xml", "https://www.tvtechnology.com/feed"],
    "tvnewscheck.com": ["https://tvnewscheck.com/feed/"],
    "newscaststudio.com": ["https://www.newscaststudio.com/feed/"],
    "ravepubs.com": ["https://ravepubs.com/feed/"],
    "avnetwork.com": ["https://www.avnetwork.com/rss", "https://www.avnetwork.com/feed"],
    "avnation.tv": ["https://www.avnation.tv/feed/"],
    "digitalavmagazine.com": ["https://www.digitalavmagazine.com/en/feed/", "https://www.digitalavmagazine.com/feed/"],
    "installation-international.com": ["https://www.installation-international.com/feed", "https://www.installation-international.com/rss"],
    "provideocoalition.com": ["https://www.provideocoalition.com/feed/"],
    "lightingandsoundamerica.com": ["https://www.lightingandsoundamerica.com/rss.asp"],
}
KEYWORDS = re.compile(
    r"\b(led|video wall|processor|screen management|switcher|atem|hyperdeck|blackmagic|barco|event master|encore3?|"
    r"e2|analog ?way|aquilon|livepremier|pixel ?hue|novastar|brompton|tessera|megapixel|helios|disguise|roe|absen|"
    r"panasonic|kairos|ross|grass valley|sony|tricaster|vizrt|vmix|for-a|evs|3play|aja|ki pro|"
    r"st ?2110|ipmx|ndi|srt|mxl|ampp|liveu|tvu|haivision|aws|cloud|remote production|replay|firmware|"
    r"live production|ob truck|projector|projection)\b", re.I)


def parse_feed(xml_text):
    root = ET.fromstring(xml_text.lstrip("﻿").strip())
    ns = {"a": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        def g(*names):
            for n in names:
                for c in it:
                    if c.tag.split("}")[-1] == n and (c.text or "").strip():
                        return c.text.strip()
            return None
        title = g("title")
        link = g("link")
        if not link:
            for c in it:
                if c.tag.split("}")[-1] == "link" and c.get("href"):
                    link = c.get("href")
                    break
        raw = g("pubDate", "published", "updated", "date")
        when = None
        if raw:
            try:
                when = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                try:
                    when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    when = None
        if title and link:
            items.append({"title": html.unescape(title)[:200], "url": link,
                          "date": when.date().isoformat() if when else None})
    return items


def collect_trade(now):
    cutoff = (now - dt.timedelta(days=10)).date().isoformat()
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "feeds": {}}
    for site, urls in TRADE_FEEDS.items():
        rec = {"ok": False, "feed_url": None, "error": None, "newest_item_date": None, "items": []}
        for u in urls:
            r = fetch(u, tries=2)
            if r["status"] != 200 or not r["text"]:
                rec["error"] = r["error"] or f"HTTP {r['status']}"
                continue
            try:
                items = parse_feed(r["text"])
            except ET.ParseError as e:
                rec["error"] = f"not a feed: {e}"
                continue
            dated = [i["date"] for i in items if i["date"]]
            rec.update(ok=True, feed_url=u, error=None,
                       newest_item_date=max(dated) if dated else None, items_in_feed=len(items))
            recent = [i for i in items if (i["date"] or "9999") >= cutoff]
            for i in recent:
                i["relevant"] = bool(KEYWORDS.search(i["title"]))
            recent.sort(key=lambda i: (not i["relevant"], i["date"] or ""), reverse=False)
            rec["items"] = sorted(recent, key=lambda i: i["date"] or "", reverse=True)[:40]
            break
        out["feeds"][site] = rec
        print(f"{'OK  ' if rec['ok'] else 'FAIL'} feed {site} newest={rec['newest_item_date']} {rec['error'] or ''}")
        time.sleep(1)
    out["summary"] = {"ok": sum(1 for f in out["feeds"].values() if f["ok"]),
                      "failed": [k for k, f in out["feeds"].items() if not f["ok"]]}
    return out


def main():
    now = dt.datetime.now(dt.timezone.utc)
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": {}}
    ordered = [x for x in SOURCES if 'evolveacademy' not in x[1]] + [x for x in SOURCES if 'evolveacademy' in x[1]]
    for sid, url, limit in ordered:
        out["sources"][sid] = collect(sid, url, limit)
        print(f"{'OK  ' if out['sources'][sid]['ok'] else 'FAIL'} {sid} "
              f"{out['sources'][sid]['http_status']} {out['sources'][sid]['error'] or ''}")
        # be gentle: evolveacademy.live rate-limits quick successive requests
        time.sleep(15 if "evolveacademy" in url else 1)
    # Blackmagic release pages: the news list is JavaScript, so probe the last 4 days.
    probes = {}
    for back in range(0, 4):
        d = (now - dt.timedelta(days=back)).strftime("%Y%m%d")
        for n in (1, 2, 3):
            u = f"https://www.blackmagicdesign.com/media/release/{d}-0{n}"
            r = fetch(u, tries=1)
            t = strip_html(r["text"]) if r["text"] else ""
            probes[f"{d}-0{n}"] = {"url": u, "http_status": r["status"],
                                   "error": r["error"], "text": t[:700]}
            if r["status"] != 200:
                break
            time.sleep(1)
    out["blackmagic_release_probes"] = probes
    n_ok = sum(1 for s in out["sources"].values() if s["ok"])
    out["summary"] = {"ok": n_ok, "failed": len(out["sources"]) - n_ok,
                      "failed_ids": [k for k, s in out["sources"].items() if not s["ok"]]}
    os.makedirs("data", exist_ok=True)
    trade = collect_trade(now)
    with open("data/trade_news.json", "w", encoding="utf-8") as f:
        json.dump(trade, f, ensure_ascii=False, indent=1)
    out["health"] = {k: {"fetched": s["ok"], "http_status": s["http_status"],
                         "newest_date_seen": s["newest_date_seen"],
                         "reading": ("FETCH FAILED" if not s["ok"] else
                                     "fetched; newest dated item " + (s["newest_date_seen"] or "unknown (no date found)"))}
                     for k, s in out["sources"].items()}
    out["health"].update({"trade:" + k: {"fetched": v["ok"], "newest_date_seen": v["newest_item_date"],
                                         "reading": "FEED FAILED: " + str(v["error"]) if not v["ok"]
                                         else "feed ok; newest item " + str(v["newest_item_date"])}
                          for k, v in trade["feeds"].items()})
    with open("data/sources.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out["summary"]))
    if n_ok == 0:
        sys.exit("No source could be fetched.")


if __name__ == "__main__":
    main()
