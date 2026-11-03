#!/usr/bin/env python3
"""Build data/news.json for the Midterms 2026 lab from politics RSS and Atom feeds.

Standard library only, so it runs on GitHub Actions without installing anything.
Sources and keyword rules live in scripts/news_config.json.

Safe by design:
  * a feed that fails is skipped with a warning; the others still count;
  * if nothing relevant is found, the previous data/news.json is kept as it is;
  * summaries are short excerpts that link back to the original story.

Usage:
  python scripts/fetch_news.py                 # normal run, writes data/news.json
  python scripts/fetch_news.py --dry-run       # print the result, write nothing
  python scripts/fetch_news.py --feeds f.json  # use another list of feeds (tests)
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "scripts" / "news_config.json"
OUT = ROOT / "data" / "news.json"
TAGS = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")
STOP = set("a an and as at be by for from has have how in is it its of on or that the this to was what who why will with".split())
FALLBACK_SUMMARY = "Open the source for the full story."


def warn(msg):
    # Shows up as a yellow annotation in the GitHub Actions run summary.
    print(f"::warning::{msg}")


def clean(text):
    text = html.unescape(TAGS.sub(" ", text or ""))
    return SPACES.sub(" ", text).strip()


def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def text_of(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    when = None
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        when = None
    if when is None:
        try:
            when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc)


def fetch(url, user_agent, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_xml(raw):
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        # Some feeds ship bare ampersands; escape them and try once more.
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"&(?!#?\w+;)", "&amp;", text)
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
        return ET.fromstring(text.encode("utf-8"))


def parse_feed(raw, feed):
    root = parse_xml(raw)
    items = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        rec = {"title": "", "link": "", "summary": "", "date": None, "source": feed["name"]}
        for ch in el:
            name = local(ch.tag)
            if name == "title" and not rec["title"]:
                rec["title"] = clean(text_of(ch))
            elif name == "link":
                href = ch.get("href")
                if href and ch.get("rel", "alternate") == "alternate" and not rec["link"]:
                    rec["link"] = href.strip()
                elif not href and text_of(ch) and not rec["link"]:
                    rec["link"] = text_of(ch).strip()
            elif name in ("description", "summary", "encoded", "content") and not rec["summary"]:
                rec["summary"] = clean(text_of(ch))
            elif name in ("pubdate", "published", "updated", "date") and rec["date"] is None:
                rec["date"] = parse_date(text_of(ch))
            elif name == "source" and feed.get("google"):
                rec["source"] = clean(text_of(ch)) or rec["source"]
        if feed.get("google"):
            # Google News titles end in " - Outlet" and the description only repeats headlines.
            suffix = " - " + rec["source"]
            if rec["title"].endswith(suffix):
                rec["title"] = rec["title"][: -len(suffix)].strip()
            rec["summary"] = ""
        if rec["title"] and rec["link"].startswith("http"):
            items.append(rec)
        if len(items) >= 60:
            break
    return items


def short_summary(summary, title, limit):
    text = summary.strip()
    if not text or text.lower().startswith(title.lower()[:40]):
        return FALLBACK_SUMMARY
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > limit:
            break
        out = (out + " " + s).strip()
    if not out:
        out = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in STOP and len(w) > 2}


def similar(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.55


def compile_rules(cfg):
    flags = re.IGNORECASE
    rules = {"require": re.compile(cfg["require"], flags), "us": re.compile(cfg["us"], flags),
             "exclude": re.compile(cfg["exclude"], flags) if cfg.get("exclude") else None,
             "cases": {}, "concepts": {}}
    for cid, c in cfg["cases"].items():
        rules["cases"][cid] = (re.compile(c["any"], flags), re.compile(c["all"], flags) if c.get("all") else None)
    for k, c in cfg["concepts"].items():
        if c.get("pattern"):
            rules["concepts"][k] = re.compile(c["pattern"], flags)
    return rules


def classify(item, rules):
    text = f"{item['title']} {item['summary']}"
    if not rules["require"].search(text) or not rules["us"].search(text):
        return None
    if rules["exclude"] and rules["exclude"].search(text):
        return None
    cases = [cid for cid, (any_re, all_re) in rules["cases"].items()
             if any_re.search(text) and (all_re is None or all_re.search(text))][:2]
    concepts = [k for k, rx in rules["concepts"].items() if rx.search(text)][:3]
    if not cases and not concepts:
        return None
    return cases, concepts


def build_story(item, cases, concepts, cfg):
    case_cfg, concept_cfg = cfg["cases"], cfg["concepts"]
    category = None
    if cases:
        category = case_cfg[cases[0]].get("category")
    if not category:
        for k in concepts:
            if concept_cfg[k].get("category"):
                category = concept_cfg[k]["category"]
                break
    if not category and re.search(r"\bgovernor", item["title"], re.IGNORECASE):
        category = "Governors"
    story = {
        "category": category or "U.S. politics",
        "title": item["title"],
        "summary": short_summary(item["summary"], item["title"], cfg.get("max_summary_chars", 240)),
        "why": concept_cfg[concepts[0]]["why"] if concepts else "",
        "source": item["source"],
        "published": item["date"].strftime("%Y-%m-%dT%H:%M:%SZ") if item["date"] else "",
        "url": item["link"],
        "concepts": concepts,
        "cases": cases,
        # 3 ("changes the reading") is an editorial judgment, so the robot never assigns it.
        "signal": 2 if cases else 1,
        "mechanism": concept_cfg[concepts[0]]["name"] if concepts else "",
        "watch": case_cfg[cases[0]]["watch"] if cases else (concept_cfg[concepts[0]].get("watch", "") if concepts else ""),
    }
    return story


def select(items, cfg, rules, now):
    scored = []
    for it in items:
        got = classify(it, rules)
        if not got:
            continue
        cases, concepts = got
        age = (now - it["date"]).total_seconds() / 3600 if it["date"] else 48
        if age < -6:  # timestamps in the future are suspicious; treat them as fresh but not newer
            age = 0
        score = 3 * len(cases) + len(concepts) + it["weight"] - 0.04 * max(age, 0)
        scored.append((score, age, it, cases, concepts))
    for window in (cfg.get("window_hours", 72), cfg.get("fallback_window_hours", 168)):
        pool = sorted([s for s in scored if s[1] <= window], key=lambda s: s[0], reverse=True)
        chosen, per_source = [], {}
        for score, age, it, cases, concepts in pool:
            if any(similar(it["title"], c[0]["title"]) or it["link"] == c[0]["link"] for c in chosen):
                continue
            if per_source.get(it["source"], 0) >= cfg.get("max_per_source", 2):
                continue
            chosen.append((it, cases, concepts))
            per_source[it["source"]] = per_source.get(it["source"], 0) + 1
            if len(chosen) >= cfg.get("max_stories", 8):
                break
        if len(chosen) >= 5 or window == cfg.get("fallback_window_hours", 168):
            return [build_story(it, cases, concepts, cfg) for it, cases, concepts in chosen]
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--feeds", help="JSON file with a list of feeds that replaces the configured ones")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump-items", help="also write every item read to this JSON file (used by results_robot.py)")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    feeds = json.loads(Path(args.feeds).read_text(encoding="utf-8")) if args.feeds else cfg["feeds"]
    rules = compile_rules(cfg)
    now = dt.datetime.now(dt.timezone.utc)

    items, ok, failed = [], [], []
    for feed in feeds:
        try:
            got = parse_feed(fetch(feed["url"], cfg["user_agent"]), feed)
            for it in got:
                it["weight"] = float(feed.get("weight", 1.0))
            items.extend(got)
            ok.append(feed["name"])
            print(f"ok     {len(got):3d} items  {feed['name']}  {feed['url'][:90]}")
        except (urllib.error.URLError, OSError, ET.ParseError, ValueError) as exc:
            failed.append(feed["name"])
            warn(f"Feed failed: {feed['name']} ({feed['url'][:80]}): {exc}")

    if args.dump_items:
        dump = [{"title": it["title"], "summary": it["summary"], "link": it["link"], "source": it["source"],
                 "date": it["date"].strftime("%Y-%m-%dT%H:%M:%SZ") if it["date"] else ""} for it in items]
        Path(args.dump_items).write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")
    stories = select(items, cfg, rules, now)
    print(f"{len(items)} items read, {len(stories)} stories kept, {len(failed)} feeds failed")
    if not stories:
        warn("No relevant stories found; keeping the previous data/news.json.")
        return 0

    payload = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "automatic-rss",
        "feeds_ok": len(ok),
        "feeds_failed": sorted(set(failed)),
        "stories": stories,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(text)
        return 0
    out = Path(args.out)
    old = out.read_text(encoding="utf-8") if out.exists() else ""
    try:
        old_stories = json.loads(old).get("stories") if old else None
    except json.JSONDecodeError:
        old_stories = None
    if old_stories == stories:
        print("Stories unchanged; leaving data/news.json as it is.")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out} with {len(stories)} stories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
