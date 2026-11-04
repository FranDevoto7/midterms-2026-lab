#!/usr/bin/env python3
"""Results robot for the Midterms 2026 lab.

It suggests race calls it finds in the news and loads them only after you confirm.

  python scripts/results_robot.py suggest ITEMS.json   # ITEMS.json comes from fetch_news.py --dump-items
  python scripts/results_robot.py sync                 # loads the suggestions you confirmed

Each suggestion becomes a GitHub issue. Closing it with "Close issue" confirms it and the
winner goes into data/results.json. Closing it as "not planned" discards it for good.
Standard library only.
"""
import datetime as dt
import email.utils
import json
from html import unescape
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "results.json"
PENDING = ROOT / "data" / "pending.json"
CANDS = json.loads((ROOT / "scripts" / "candidates.json").read_text(encoding="utf-8"))
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
MARK = re.compile(r"<!--\s*midterms-result\s+(\{.*?\})\s*-->", re.S)
MAX_NEW = 8

# A winner verb right after the candidate's name, or an outlet projecting the race.
WIN = (r"(?:wins?|won|defeats?|defeated|beats?|unseats?|ousts?|holds?|keeps?|retains?|flips?|"
       r"re-?elected|reelected|elected|declared (?:the )?winner|projected (?:to win|winner|the winner|to be re-?elected))")
PROJ = r"(?:AP|Associated Press|NBC News|NBC|CBS News|CBS|CNN|ABC News|ABC|Fox News|Decision Desk(?: HQ)?|DDHQ)\s+(?:projects?|calls?|declares?)"
# Headlines that talk about a race without settling it.
NEG = re.compile(r"\b(?:primar(?:y|ies)|runoff|debates?|endorse\w*|polls? show|forecast\w*|predict\w*|could|would|might|may|if|"
                 r"leads?|leading|ahead|tighten\w*|too close|vows?|hopes?|seeks?|rall(?:y|ies)|nominat\w*)\b|\?", re.I)
# Surnames that are also ordinary words need the full name to count.
COMMON = {"Love", "Little", "Green", "Sand", "Cotton", "Hill", "Hunt", "Charles", "Scott", "Bailey", "Hull"}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def warn(msg):
    print(f"::warning::{msg}")


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))


def parse_date(value):
    if not value:
        return None
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(str(value))
        except (TypeError, ValueError, IndexError):
            return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def surname(name):
    parts = [p for p in name.replace(",", " ").split() if p.rstrip(".") not in ("Jr", "Sr", "II", "III", "IV")]
    return parts[-1] if parts else name


def name_pattern(name):
    full = norm(name)
    alts = [re.escape(full)]
    last = surname(full)
    if last != full and last not in COMMON:
        alts.append(re.escape(last))
    return "(?:" + "|".join(alts) + ")"


def detect(items):
    """Return {key: suggestion} for headlines that name one winner in a known race."""
    not_before = parse_date(CANDS["not_before"])
    names = CANDS["state_names"]
    found = {}
    for it in items:
        when = parse_date(it.get("date"))
        if not when or when < not_before:
            continue
        title = norm(it.get("title", ""))
        text = norm(title + " " + (it.get("summary") or ""))
        if not title or NEG.search(title):
            continue
        for office in ("senate", "governors"):
            for st, cands in CANDS[office].items():
                if not re.search(r"\b" + re.escape(names[st]) + r"\b", text):
                    continue
                winners, conceded = {}, set()
                for name, party in cands:
                    nm = name_pattern(name)
                    if (re.search(rf"\b{nm}\b(?:['’]s)?(?:\s+\S+){{0,3}}?\s+(?i:{WIN})\b", title)
                            or re.search(rf"(?i:{PROJ})(?:\s+\S+){{0,4}}?\s+{nm}\b", title)):
                        winners[party] = name
                    elif re.search(rf"\b{nm}\b\s+(?i:concede[sd]?)\b", title):
                        conceded.add(party)
                parties = {p for _, p in cands}
                if not winners and len(conceded) == 1 and len(parties) == 2:
                    p = (parties - conceded).pop()
                    winners = {p: next(n for n, q in cands if q == p)}
                if len(winners) != 1:
                    continue
                party, cand = next(iter(winners.items()))
                key = f"{office}:{st}:{party}"
                s = found.setdefault(key, {"office": office, "state": st, "winner": party, "candidate": cand,
                                           "title": it.get("title", ""), "url": it.get("link", ""),
                                           "source": it.get("source", ""), "date": it.get("date", ""),
                                           "outlets": [], "ap": False})
                outlet = (it.get("source") or "").strip()
                if outlet and outlet not in s["outlets"]:
                    s["outlets"].append(outlet)
                if re.search(r"\b(?:AP|Associated Press)\b", outlet + " " + (it.get("title") or "")):
                    s["ap"] = True
    return found


def gh(method, path, payload=None):
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                 "User-Agent": "midterms-lab-results-robot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def issue_text(s, auto=False):
    state = CANDS["state_names"][s["state"]]
    race = f"la carrera al Senado en {state}" if s["office"] == "senate" else f"la gobernación de {state}"
    title = (f"Cargado: {s['candidate']} ({s['winner']}) gana {race}" if auto
             else f"Confirmar: {s['candidate']} ({s['winner']}) gana {race}")
    meta = {k: s[k] for k in ("office", "state", "winner", "source", "url")}
    if auto:
        who = ", ".join(s.get("outlets", [])[:3]) or s["source"]
        return title, (
            f"El robot cargó este resultado en el sitio porque lo anuncian {who}:\n\n"
            f"**{s['title']}**\n{s['source']}, {s['date']}\n{s['url']}\n\n"
            "- Si está bien, no hace falta hacer nada. Si querés, sumá una descripción en un comentario.\n"
            "- Si está mal, tocá la flechita al lado de **Close issue** y elegí **Close as not planned**: el resultado se borra del sitio.\n\n"
            f"<!-- midterms-result {json.dumps(meta, ensure_ascii=False)} -->\n")
    body = (
        "El robot encontró esta noticia y cree que ya hay un ganador:\n\n"
        f"**{s['title']}**\n{s['source']}, {s['date']}\n{s['url']}\n\n"
        "Abrí el enlace y fijate que la noticia diga eso.\n\n"
        "- Si es correcto, tocá **Close issue**: el resultado se carga solo en el sitio en un par de minutos.\n"
        "- Si querés sumar una descripción para el sitio, escribila en un comentario antes de cerrar: el botón pasa a decir **Close with comment**.\n"
        "- Si no, tocá la flechita al lado de **Close issue** y elegí **Close as not planned**: el robot no lo vuelve a sugerir.\n\n"
        f"<!-- midterms-result {json.dumps(meta, ensure_ascii=False)} -->\n")
    return title, body


def suggest(items_path):
    items = load(Path(items_path), [])
    found = detect(items)
    print(f"{len(items)} news items checked, {len(found)} possible calls found")
    results = load(RESULTS, {})
    for k in ("cases", "senate", "governors"):
        if not isinstance(results.get(k), dict):
            results[k] = {}
    pending = load(PENDING, {"suggestions": {}})
    pending.setdefault("suggestions", {})
    races = {}
    for key, s in found.items():
        races.setdefault(f"{s['office']}:{s['state']}", []).append(key)
    opened, loaded = 0, 0
    for key, s in found.items():
        if key in pending["suggestions"] or results[s["office"]].get(s["state"]):
            continue
        if opened >= MAX_NEW:
            break
        conflict = len(races[f"{s['office']}:{s['state']}"]) > 1
        auto = not conflict and (s["ap"] or len(s["outlets"]) >= 2)
        title, body = issue_text(s, auto)
        if not (TOKEN and REPO):
            print(f"(dry run, no GitHub token) would {'load' if auto else 'suggest'}: {title}")
            continue
        try:
            issue = gh("POST", f"/repos/{REPO}/issues", {"title": title, "body": body})
        except urllib.error.URLError as exc:
            warn(f"Could not open an issue for {key}: {exc}")
            continue
        status = "open"
        if auto:
            entry = {"winner": s["winner"], "source": ", ".join(s["outlets"][:3]) or s["source"], "auto": True, "at": now_iso(), "issue": issue.get("number")}
            if s.get("url"):
                entry["url"] = s["url"]
            results[s["office"]][s["state"]] = entry
            status, loaded = "auto", loaded + 1
        pending["suggestions"][key] = dict(s, issue=issue.get("number"), status=status, opened=now_iso())
        opened += 1
        print(f"{'Loaded' if auto else 'Suggested'} #{issue.get('number')}: {title}")
    if loaded:
        results["updated_at"] = now_iso()
        save(RESULTS, results)
    m_opened = suggest_milestones(items, results, pending)
    h_loaded = detect_house_control(items, results, pending)
    if h_loaded:
        results["updated_at"] = now_iso()
        save(RESULTS, results)
    if opened or m_opened or h_loaded:
        save(PENDING, pending)
    update_house()


HOUSE_PAGE = "2026_United_States_House_of_Representatives_elections"
HOUSE_API = ("https://en.wikipedia.org/w/api.php?action=parse&prop=wikitext&format=json&formatversion=2&section=0&page=" + HOUSE_PAGE)
REP_SEATS_2024 = 220  # seats Republicans won in 2024, the baseline for the midterm seat change


def parse_house(wikitext):
    """Seats won so far and the seat change from the election infobox, if the editors have filled them in."""
    params = {}
    for m in re.finditer(r"^\s*\|\s*([a-z_]+?)(\d)\s*=\s*(.*?)\s*$", wikitext or "", flags=re.M):
        params.setdefault(m.group(2), {})[m.group(1)] = m.group(3)

    def clean(v):
        return re.sub(r"<ref[^>]*/>|<ref.*?</ref>|'{2,3}", "", v or "", flags=re.S)

    def num(v):
        m = re.search(r"\d+", clean(v))
        return int(m.group()) if m else None

    def change(v):
        v = clean(v)
        if not v.strip():
            return None
        if re.search(r"steady|no ?change", v, re.I):
            return 0
        n = num(v)
        if n is None:
            return None
        return -n if re.search(r"decrease|loss|−|-\s*\d", v, re.I) else n

    by_party = {}
    for p in params.values():
        name = p.get("party", "")
        key = "R" if "Republican" in name else "D" if "Democratic" in name else None
        if key and key not in by_party:
            by_party[key] = p
    if "R" not in by_party or "D" not in by_party:
        return None
    won = lambda p: p.get("seats") or p.get("seats_after") or p.get("seats_won")
    seats = {k: num(won(v)) for k, v in by_party.items()}
    others = sum(num(won(p)) or 0 for p in params.values() if not any(p is q for q in by_party.values()))
    return {"R": seats["R"], "D": seats["D"], "others": others, "changeR": change(by_party["R"].get("seat_change"))}


BALLOTPEDIA_HUB = "https://ballotpedia.org/Ballotpedia%27s_Election_Analysis_Hub,_2026"
UA = {"User-Agent": "midterms-2026-lab/1.0 (results robot; github.com)"}

# "Democrats win control of the House", "Republicans keep their House majority", "House: Democrats capture majority"
HOUSE_CTRL = [
    re.compile(r"\b(democrats?|republicans?|gop)\b[^.?!:;]{0,60}?\b(?:wins?|won|takes?|took|captures?|captured|clinch(?:es|ed)?|flips?|flipped|retakes?|retook|keeps?|kept|holds?|held|retains?|retained)\b"
               r"[^.?!:;]{0,30}?\b(?:control of the (?:u\.?s\.? )?house|(?:u\.?s\.? )?house majority|majority in the (?:u\.?s\.? )?house|the (?:u\.?s\.? )?house)\b", re.I),
    re.compile(r"\bhouse\b[^.?!]{0,80}?\b(democrats?|republicans?|gop)\b[^.?!:;]{0,30}?\b(?:wins?|won|takes?|took|captures?|captured|clinch(?:es|ed)?|flips?|flipped|retakes?|retook|keeps?|kept|holds?|held|retains?|retained)\b"
               r"[^.?!:;]{0,20}?\b(?:control|(?:the |a |their )?majority)\b", re.I),
]
SPECULATIVE = re.compile(r"\b(could|may|might|would|poised|on track|expected|likely|favou?red|hopes?|aims?|seeks?|path to|needs?|if|whether|race for|battle for|fight for|chances?|odds|forecast|predict|polls?)\b", re.I)


def house_control_party(title):
    if SPECULATIVE.search(title) or re.search(r"\bwhite house\b", title, re.I) and not re.search(r"control of the house|house majority", title, re.I):
        return None
    for rx in HOUSE_CTRL:
        m = rx.search(title)
        if m:
            return "D" if m.group(1).lower().startswith("dem") else "R"
    return None


def detect_house_control(items, results, pending):
    """Load who controls the House when AP or at least two outlets say it in a headline."""
    if isinstance((results.get("house") or {}).get("US"), dict):
        return False
    sugg = pending.setdefault("suggestions", {})
    if any(k.startswith("house:US:") for k in sugg):
        return False
    not_before = parse_date(CANDS["not_before"])
    votes = {}
    for it in items:
        when = parse_date(it.get("date"))
        if not when or when < not_before:
            continue
        party = house_control_party(norm(it.get("title", "")))
        if party:
            votes.setdefault(party, []).append(it)
    if len(votes) != 1:
        if len(votes) > 1:
            warn("House control: headlines disagree; waiting.")
        return False
    party, its = next(iter(votes.items()))
    outlets = sorted({(it.get("source") or "").strip() for it in its if (it.get("source") or "").strip()})
    ap = any("associated press" in (it.get("source") or "").lower() or "apnews.com" in (it.get("link") or "") for it in its)
    if not (ap or len(outlets) >= 2):
        print(f"House control: one outlet so far ({', '.join(outlets)}); waiting for a second.")
        return False
    who = "los demócratas" if party == "D" else "los republicanos"
    entry = {"winner": party, "source": ", ".join(outlets[:3]), "url": its[0].get("link", ""), "auto": True, "at": now_iso()}
    meta = {"office": "house", "state": "US", "winner": party, "source": entry["source"], "url": entry["url"]}
    number = None
    if TOKEN and REPO:
        lines = "\n".join(f"- {it.get('title', '')} ({it.get('source', '')}) {it.get('link', '')}" for it in its[:5])
        body = (f"El robot cargó que {who} ganan el control de la Cámara, porque lo dicen estos titulares:\n\n{lines}\n\n"
                "Ya aparece en el sitio. Si está mal, tocá la flechita al lado de **Close issue** y elegí **Close as not planned**: se borra en la próxima pasada.\n\n"
                f"<!-- midterms-result {json.dumps(meta, ensure_ascii=False)} -->\n")
        try:
            issue = gh("POST", f"/repos/{REPO}/issues", {"title": f"Cargado: {who} ganan el control de la Cámara", "body": body})
            number = issue.get("number")
        except (urllib.error.URLError, OSError) as exc:
            warn(f"Could not open the House control notice: {exc}")
    if number:
        entry["issue"] = number
    results.setdefault("house", {})["US"] = entry
    sugg[f"house:US:{party}"] = dict(meta, issue=number, status="auto", opened=now_iso())
    print(f"House control loaded: {party} ({entry['source']})")
    return True


def parse_ballotpedia(html):
    """Seats after the election from Ballotpedia's 'U.S. House Partisan Breakdown' table, or None while it says TBD."""
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or "")))
    i = text.find("U.S. House Partisan Breakdown")
    if i < 0:
        return None
    seg = text[i:i + 2000]

    def after(label):
        m = re.search(label + r"\s+(\d{1,3})\s+\*?(\d{1,3}|TBD)\b", seg, re.I)
        return m.group(2) if m else None

    d, r, ind = after(r"Democratic Party"), after(r"Republican Party"), after(r"Independent")
    if not (d and r and d.isdigit() and r.isdigit()):
        return None
    D, R, I = int(d), int(r), int(ind) if ind and ind.isdigit() else 0
    if not (100 <= D <= 335 and 100 <= R <= 335) or D + R + I > 435:
        return None
    return {"D": D, "R": R, "called": D + R + I}


def house_from_wikipedia():
    try:
        req = urllib.request.Request(HOUSE_API, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = json.loads(resp.read()).get("parse", {}).get("wikitext", "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        warn(f"Could not read the House count from Wikipedia: {exc}")
        return None
    h = parse_house(text)
    if not h or h["R"] is None or h["D"] is None:
        return None
    called = h["R"] + h["D"] + h["others"]
    if called >= 430:
        net = h["R"] - REP_SEATS_2024
    elif h["changeR"] is not None:
        net = h["changeR"]
    else:
        return None
    return {"net": net, "called": called, "source": "Wikipedia", "url": "https://en.wikipedia.org/wiki/" + HOUSE_PAGE}


def house_from_ballotpedia():
    try:
        req = urllib.request.Request(BALLOTPEDIA_HUB, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        warn(f"Could not read the House count from Ballotpedia: {exc}")
        return None
    b = parse_ballotpedia(html)
    if not b:
        return None
    return {"net": b["R"] - REP_SEATS_2024, "called": b["called"], "source": "Ballotpedia", "url": BALLOTPEDIA_HUB}


def update_house():
    results = load(RESULTS, {})
    if results.get("house_from") == "form":
        return
    found = [x for x in (house_from_wikipedia(), house_from_ballotpedia()) if x and abs(x["net"]) <= 150 and 0 < x["called"] <= 435]
    if not found:
        print("House: neither Wikipedia nor Ballotpedia has a seat count yet.")
        return
    best = max(found, key=lambda x: x["called"])
    old = results.get("house_auto") or {}
    if results.get("house_net_change_presidents_party") == best["net"] and old.get("called") == best["called"] and old.get("source") == best["source"]:
        return
    results["house_net_change_presidents_party"] = best["net"]
    results["house_auto"] = {"source": best["source"], "called": best["called"], "url": best["url"], "at": now_iso()}
    results["updated_at"] = now_iso()
    save(RESULTS, results)
    print(f"House: net change {best['net']:+d} for the president's party, {best['called']} of 435 seats decided ({best['source']}).")


def clean_note(text, limit=1500):
    """Plain text from a GitHub comment: no Markdown marks, tidy paragraphs, cut at a word if very long."""
    t = (text or "").replace("\r\n", "\n")
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^[ \t]{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])[ \t]+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"\1", t)
    t = re.sub(r"~~(.+?)~~", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", t)]
    t = "\n\n".join(p for p in paras if p)
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
    return t


def event_issue_number():
    """The issue behind this run, when an issue was closed or commented on."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    try:
        ev = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return None
    return (ev.get("issue") or {}).get("number")


def comment(number, text):
    try:
        gh("POST", f"/repos/{REPO}/issues/{number}/comments", {"body": text})
    except (urllib.error.URLError, OSError):
        pass


HITO = re.compile(r"^[ \t]*hito[ \t]*[:\-–—][ \t]*(.+?)[ \t]*$", re.I | re.M)
MARK_H = re.compile(r"<!--\s*midterms-milestone\s+(\{.*?\})\s*-->", re.S)
# Headlines that describe a winner as a first or as history-making.
MILESTONE = re.compile(
    r"\b(?:first|1st)\b(?:\W+\w+){0,6}?\W+(?:woman|women|female|Black|African[- ]American|Latina|Latino|Hispanic|"
    r"Asian[- ]American|Native American|Indigenous|openly (?:gay|lesbian|bisexual)|LGBTQ|transgender|Muslim)\b"
    r"|\bmakes? history\b|\bhistory[- ]making\b|\bhistoric (?:first|win|victory)\b", re.I)
MAX_MILESTONES = 6


def milestone_office(title):
    """The office the milestone phrase refers to: the office word closest to it ("first Black governor")."""
    m = MILESTONE.search(title)
    if not m:
        return None
    spots = []
    for rx, office in ((r"\bsenat", "senate"), (r"\b(?:governor|gubernatorial)", "governors")):
        for x in re.finditer(rx, title, re.I):
            d = 0 if m.start() <= x.start() <= m.end() + 12 else min(abs(x.start() - m.start()), abs(x.start() - m.end()))
            spots.append((d, office))
    return min(spots)[1] if spots else None


def owner_texts(number):
    """(description, milestone) from your comments on a notice. A line that starts with "Hito:" is the milestone."""
    try:
        comments = gh("GET", f"/repos/{REPO}/issues/{number}/comments?per_page=100") or []
    except (urllib.error.URLError, OSError):
        return None
    trusted = ("OWNER", "MEMBER", "COLLABORATOR")
    note, milestone = "", ""
    for c in comments:
        if (c.get("user") or {}).get("type") == "Bot" or c.get("author_association") not in trusted:
            continue
        body = c.get("body") or ""
        found = HITO.findall(body)
        if found:
            milestone = clean_note(found[-1], 300)
        rest = HITO.sub("", body).strip()
        if rest:
            note = clean_note(rest)
    return note, milestone


def latest_note(number):
    got = owner_texts(number)
    return None if got is None else got[0]


def robot_issues():
    """Every notice the robot opened, read straight from GitHub, open and closed: results and milestones."""
    out = []
    for page in range(1, 8):
        try:
            batch = gh("GET", f"/repos/{REPO}/issues?state=all&per_page=100&page={page}") or []
        except (urllib.error.URLError, OSError) as exc:
            warn(f"Could not list the issues: {exc}")
            return None
        for it in batch:
            if "pull_request" in it:
                continue
            body = it.get("body") or ""
            for rx, kind in ((MARK, "result"), (MARK_H, "milestone")):
                m = rx.search(body)
                if not m:
                    continue
                try:
                    meta = json.loads(m.group(1))
                except ValueError:
                    break
                if (meta.get("office") in ("senate", "governors") or (kind == "result" and meta.get("office") == "house")) \
                        and meta.get("state") and meta.get("winner") in ("D", "R", "I"):
                    out.append((it, meta, kind))
                break
        if len(batch) < 100:
            break
    return out


def suggest_milestones(items, results, pending):
    """Open a "Posible hito" notice when a headline calls a confirmed winner a first or history-making."""
    mil = pending.setdefault("milestones", {})
    names = CANDS["state_names"]
    opened = 0
    for office in ("senate", "governors"):
        for st, e in (results.get(office) or {}).items():
            if opened >= MAX_MILESTONES:
                return opened
            key = f"{office}:{st}"
            if not isinstance(e, dict) or e.get("milestone") or key in mil:
                continue
            w = e.get("winner")
            win = next((n for n, p in CANDS[office].get(st, []) if p == w), None)
            if not win:
                continue
            nm, sname = name_pattern(win), names.get(st, st)
            full, last = norm(win), surname(norm(win))
            # Other candidates who share the winner's surname (Troy Jackson in Maine, Rick Jackson in Georgia).
            namesakes = [norm(n) for o in ("senate", "governors") for cs in CANDS[o].values() for n, _ in cs
                         if surname(norm(n)) == last and norm(n) != full]
            cue = r"\bsenat" if office == "senate" else r"\b(?:governor|gubernatorial)"
            for it in items:
                title = norm(it.get("title", ""))
                if not MILESTONE.search(title) or not re.search(rf"\b{nm}\b", title):
                    continue
                if full not in title and any(n in title for n in namesakes):
                    continue  # the surname belongs to someone else in this headline
                said = milestone_office(title)
                if said and said != office:
                    continue  # "first Black governor" is about the governorship, not about a senator in the same headline
                text = norm(title + " " + (it.get("summary") or ""))
                if not re.search(cue, text, re.I):
                    continue  # the headline must be about this office
                if full not in title and not re.search(r"\b" + re.escape(sname) + r"\b", text):
                    continue  # with only the surname, it must also name the state
                meta = {"office": office, "state": st, "winner": w, "title": it.get("title", ""), "source": it.get("source", ""), "url": it.get("link", "")}
                race = f"el Senado por {sname}" if office == "senate" else f"la gobernación de {sname}"
                title_i = f"Posible hito: {win} ({race})"
                body = ("Un titular sobre el ganador de esta carrera habla de un posible hito:\n\n"
                        f"**{meta['title']}**\n{meta['source']}, {it.get('date', '')}\n{meta['url']}\n\n"
                        "- Si es un hito, escribí un comentario que empiece con **Hito:** y tu texto (por ejemplo: «Hito: primera mujer negra elegida gobernadora del país») y cerrá el aviso. Si lo cerrás sin escribir nada, el sitio muestra este titular como hito.\n"
                        "- Si no lo es, tocá la flechita al lado de **Close issue** y elegí **Close as not planned**.\n\n"
                        f"<!-- midterms-milestone {json.dumps(meta, ensure_ascii=False)} -->\n")
                if not (TOKEN and REPO):
                    print(f"(dry run, no GitHub token) would open: {title_i}")
                    break
                try:
                    issue = gh("POST", f"/repos/{REPO}/issues", {"title": title_i, "body": body})
                except (urllib.error.URLError, OSError) as exc:
                    warn(f"Could not open a milestone notice for {key}: {exc}")
                    break
                mil[key] = dict(meta, issue=issue.get("number"), status="open", opened=now_iso())
                opened += 1
                print(f"Possible milestone #{issue.get('number')}: {title_i}")
                break
    return opened


def sync_milestones(m_issues, pending, results):
    """Apply what you did with each "Posible hito" notice. Returns (pending changed, results changed)."""
    mil = pending.setdefault("milestones", {})
    p_changed = r_changed = False
    known = {v.get("issue") for v in mil.values()}
    for it, meta in m_issues:
        if it["number"] in known:
            continue
        key = f"{meta['office']}:{meta['state']}"
        if key in mil:
            key = f"{key}#{it['number']}"
        mil[key] = dict(meta, issue=it["number"], status="open", adopted=now_iso())
        p_changed = True
    by_num = {it["number"]: it for it, _ in m_issues}
    for key, m in mil.items():
        it = by_num.get(m.get("issue"))
        if not it or m.get("status") not in ("open", "confirmed"):
            continue
        closed = it.get("state") == "closed"
        rejected = closed and it.get("state_reason") not in ("completed", None)
        if rejected:
            if m["status"] == "confirmed":
                entry = (results.get(m["office"]) or {}).get(m["state"])
                if isinstance(entry, dict) and entry.get("milestone_issue") == it["number"]:
                    for k in ("milestone", "milestone_url", "milestone_issue"):
                        entry.pop(k, None)
                    r_changed = True
                    comment(it["number"], "Quitado: el hito ya no aparece en el sitio.")
                    print(f"Milestone removed for {key} (notice #{it['number']})")
            if m["status"] in ("open", "confirmed"):
                m["status"], p_changed = "rejected", True
            continue
        entry = (results.get(m["office"]) or {}).get(m["state"])
        if not isinstance(entry, dict) or entry.get("winner") != m.get("winner") or entry.get("milestone_from") == "form":
            continue
        stamp = it.get("updated_at") or ""
        newly = closed and m["status"] == "open"
        if m.get("seen") == stamp and not newly:
            continue
        got = owner_texts(it["number"])
        m["seen"], p_changed = stamp, True
        text = got[1] if got else ""
        if not text and newly and m.get("title"):
            text = f"{m.get('source') or 'Según la prensa'}: “{m['title']}”"
        if text and entry.get("milestone") != text:
            entry["milestone"] = text
            entry["milestone_issue"] = it["number"]
            if m.get("url"):
                entry["milestone_url"] = m["url"]
            r_changed = True
            print(f"Milestone for {key}: {text}")
        if newly:
            m["status"] = "confirmed"
            comment(it["number"], "Listo: el hito ya aparece en el sitio. Si escribís otro comentario con «Hito: …», el texto se actualiza.")
    return p_changed, r_changed

CORRECTIONS = [ROOT / "data" / "corrections.json", ROOT / "corrections.json"]


def apply_corrections():
    """A one-time batch of hand-written fixes (corrections.json). Applied once, then the file is removed."""
    path = next((p for p in CORRECTIONS if p.exists()), None)
    if not path:
        return
    try:
        fixes = json.loads(path.read_text(encoding="utf-8")).get("fixes", [])
    except (OSError, ValueError) as exc:
        warn(f"corrections.json could not be read: {exc}")
        return
    results = load(RESULTS, {})
    done = 0
    for f in fixes:
        office, st = f.get("office"), f.get("state")
        entry = (results.get(office) or {}).get(st)
        if not isinstance(entry, dict):
            if not (f.get("create") and office in ("senate", "governors") and f.get("winner") in ("D", "R", "I")):
                warn(f"Correction skipped: there is no {office} result for {st}.")
                continue
            entry = {"winner": f["winner"]}
            for k in ("source", "url", "at"):
                if f.get(k):
                    entry[k] = f[k]
            results.setdefault(office, {})[st] = entry
            print(f"Added {office} {st} ({f['winner']})")
        if f.get("winner") and entry.get("winner") != f["winner"]:
            warn(f"Correction skipped for {office} {st}: the winner on file is {entry.get('winner')}, not {f['winner']}.")
            continue
        if "milestone_url" in f:
            if f["milestone_url"]:
                entry["milestone_url"] = f["milestone_url"]
            else:
                entry.pop("milestone_url", None)
        if "milestone" in f:
            if f["milestone"]:
                entry["milestone"] = f["milestone"]
            else:
                for k in ("milestone", "milestone_url", "milestone_issue"):
                    entry.pop(k, None)
            entry["milestone_from"] = "form"
        if "note" in f:
            if f["note"]:
                entry["note"] = f["note"]
            else:
                entry.pop("note", None)
            entry["note_from"] = "form"
        if f.get("source"):
            entry["source"] = f["source"]
        done += 1
        print(f"Corrected {office} {st}")
    if done:
        results["updated_at"] = now_iso()
        save(RESULTS, results)
    path.unlink()
    print(f"Applied {done} of {len(fixes)} corrections and removed {path.name}.")


def sync():
    apply_corrections()
    if not (TOKEN and REPO):
        print("No GitHub token; skipping the sync.")
        return
    every = robot_issues()
    if every is None:
        return
    issues = [(it, meta) for it, meta, kind in every if kind == "result"]
    m_issues = [(it, meta) for it, meta, kind in every if kind == "milestone"]
    pending = load(PENDING, {"suggestions": {}})
    sugg = pending.setdefault("suggestions", {})
    results = load(RESULTS, {})
    for k in ("cases", "senate", "governors", "house"):
        if not isinstance(results.get(k), dict):
            results[k] = {}
    p_changed = r_changed = False

    # 1. Notices whose record was lost (for example, after a failed run) are adopted again.
    known = {v.get("issue") for v in sugg.values()}
    for it, meta in issues:
        if it["number"] in known:
            continue
        key = f"{meta['office']}:{meta['state']}:{meta['winner']}"
        if key in sugg:
            key = f"{key}#{it['number']}"
        sugg[key] = {"office": meta["office"], "state": meta["state"], "winner": meta["winner"],
                     "source": meta.get("source", ""), "url": meta.get("url", ""), "issue": it["number"],
                     "status": "auto" if (it.get("title") or "").startswith("Cargado") else "open",
                     "adopted": now_iso()}
        p_changed = True
        print(f"Recovered notice #{it['number']} ({key})")

    by_num = {it["number"]: it for it, _ in issues}
    focus = event_issue_number()
    for key, s in sugg.items():
        it = by_num.get(s.get("issue"))
        if not it or s.get("status") not in ("open", "auto", "confirmed"):
            continue
        office, st, w = s["office"], s["state"], s["winner"]
        closed = it.get("state") == "closed"
        rejected = closed and it.get("state_reason") not in ("completed", None)
        just = False

        # 2. Closed as "not planned": a suggestion is discarded, an automatic load is removed.
        if s["status"] in ("open", "auto") and rejected:
            entry = results[office].get(st)
            if s["status"] == "auto" and isinstance(entry, dict) and entry.get("auto") and entry.get("winner") == w:
                results[office].pop(st, None)
                r_changed = True
                comment(it["number"], "Borrado: el resultado ya no aparece en el sitio.")
            else:
                comment(it["number"], "Descartado: el robot no va a volver a sugerir este resultado.")
            s["status"], s["closed"], p_changed = "rejected", now_iso(), True
            print(f"Rejected {key} (notice #{it['number']})")
            continue

        # 3. A suggestion you closed with "Close issue" is a confirmed result.
        if s["status"] == "open" and closed:
            entry = results[office].get(st)
            if not (isinstance(entry, dict) and entry.get("winner") == w):
                new = {"winner": w}
                if s.get("source"):
                    new["source"] = s["source"]
                if s.get("url"):
                    new["url"] = s["url"]
                new["at"] = it.get("closed_at") or now_iso()
                new["issue"] = it["number"]
                results[office][st] = new
                r_changed = True
            s["status"], s["closed"], p_changed, just = "confirmed", now_iso(), True, True
            comment(it["number"], "Listo: el resultado ya está cargado en el sitio. Si editás tu comentario o agregás otro, la descripción se actualiza sola.")
            print(f"Confirmed {key} (notice #{it['number']})")

        # 4. An automatic load whose record was lost is put back; closing it as done confirms it.
        if s["status"] == "auto":
            entry = results[office].get(st)
            if not isinstance(entry, dict):
                entry = {"winner": w, "source": s.get("source", ""), "auto": True, "at": it.get("created_at") or now_iso(), "issue": it["number"]}
                if s.get("url"):
                    entry["url"] = s["url"]
                results[office][st] = entry
                r_changed, just = True, True
                print(f"Restored {key} (notice #{it['number']})")
            if closed:
                s["status"], p_changed = "confirmed", True
                if entry.get("winner") == w and entry.pop("auto", None) is not None:
                    r_changed = True

        # 4b. A result you confirmed that went missing from the data file is put back,
        #     unless you removed it on purpose with "clear".
        if s["status"] == "confirmed" and closed and results[office].get(st) is None and not (results.get("cleared") or {}).get(f"{office}:{st}"):
            new = {"winner": w}
            if s.get("source"):
                new["source"] = s["source"]
            if s.get("url"):
                new["url"] = s["url"]
            new["at"], new["issue"] = it.get("closed_at") or now_iso(), it["number"]
            results[office][st] = new
            r_changed, just = True, True
            print(f"Restored the confirmed result {key} (notice #{it['number']})")

        # 5. Descriptions follow your comments. Only notices that changed are read, to keep it light.
        entry = results[office].get(st)
        if s["status"] not in ("confirmed", "auto") or not isinstance(entry, dict) or entry.get("winner") != w or entry.get("note_from") == "form":
            continue
        owner = entry.get("issue")
        if (owner is not None and owner != it["number"]) or (owner is None and "#" in key):
            continue  # a duplicate notice: the description comes from the one that loaded the result
        stamp = it.get("updated_at") or ""
        if not just:
            if focus is not None and focus != it["number"]:
                continue
            if focus is None and s.get("seen") == stamp:
                continue
        got = owner_texts(it["number"])
        s["seen"], p_changed = stamp, True
        if got is None:
            continue
        note, milestone = got
        if note != entry.get("note", ""):
            if note:
                entry["note"] = note
            else:
                entry.pop("note", None)
            r_changed = True
            print(f"Updated the description of {key} from notice #{it['number']}")
        if milestone and milestone != entry.get("milestone") and entry.get("milestone_from") != "form":
            entry["milestone"] = milestone
            r_changed = True
            print(f"Marked a milestone for {key} from notice #{it['number']}")

    mp, mr = sync_milestones(m_issues, pending, results)
    p_changed, r_changed = p_changed or mp, r_changed or mr
    if r_changed:
        results["updated_at"] = now_iso()
        save(RESULTS, results)
    if p_changed or r_changed:
        save(PENDING, pending)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "suggest":
        suggest(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "sync":
        sync()
    else:
        print(__doc__)
        sys.exit(2)
