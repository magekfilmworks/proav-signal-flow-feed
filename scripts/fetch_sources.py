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
import re
import sys
import time
import urllib.request

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


def fetch(url, tries=3):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read(3_000_000)
                enc = r.headers.get_content_charset() or "utf-8"
                return {"status": r.status, "error": None,
                        "text": body.decode(enc, errors="replace")}
        except urllib.error.HTTPError as e:
            last = {"status": e.code, "error": f"HTTP {e.code}", "text": ""}
            if e.code in (403, 404, 410):
                break
        except Exception as e:  # noqa: BLE001
            last = {"status": None, "error": f"{type(e).__name__}: {e}", "text": ""}
        time.sleep(3 * (n + 1))
    return last


def collect(sid, url, limit):
    res = fetch(url)
    text = strip_html(res["text"]) if res["text"] else ""
    ok = res["status"] == 200 and len(text) > 200
    return {"url": url, "ok": ok, "http_status": res["status"], "error": res["error"],
            "chars_total": len(text), "truncated": len(text) > limit,
            "text": text[:limit]}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": {}}
    for sid, url, limit in SOURCES:
        out["sources"][sid] = collect(sid, url, limit)
        print(f"{'OK  ' if out['sources'][sid]['ok'] else 'FAIL'} {sid} "
              f"{out['sources'][sid]['http_status']} {out['sources'][sid]['error'] or ''}")
        time.sleep(1)
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
    with open("data/sources.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out["summary"]))
    if n_ok == 0:
        sys.exit("No source could be fetched.")


if __name__ == "__main__":
    main()
