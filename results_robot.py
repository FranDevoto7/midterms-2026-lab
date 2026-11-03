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
                found.setdefault(key, {"office": office, "state": st, "winner": party, "candidate": cand,
                                       "title": it.get("title", ""), "url": it.get("link", ""),
                                       "source": it.get("source", ""), "date": it.get("date", "")})
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


def issue_text(s):
    state = CANDS["state_names"][s["state"]]
    race = f"la carrera al Senado en {state}" if s["office"] == "senate" else f"la gobernación de {state}"
    title = f"Confirmar: {s['candidate']} ({s['winner']}) gana {race}"
    meta = {k: s[k] for k in ("office", "state", "winner", "source", "url")}
    body = (
        "El robot encontró esta noticia y cree que ya hay un ganador:\n\n"
        f"**{s['title']}**\n{s['source']}, {s['date']}\n{s['url']}\n\n"
        "Abrí el enlace y fijate que la noticia diga eso.\n\n"
        "- Si es correcto, tocá **Close issue**: el resultado se carga solo en el sitio en un par de minutos.\n"
        "- Si no, tocá la flechita al lado de **Close issue** y elegí **Close as not planned**: el robot no lo vuelve a sugerir.\n\n"
        f"<!-- midterms-result {json.dumps(meta, ensure_ascii=False)} -->\n")
    return title, body


def suggest(items_path):
    items = load(Path(items_path), [])
    found = detect(items)
    print(f"{len(items)} news items checked, {len(found)} possible calls found")
    if not found:
        return
    results = load(RESULTS, {})
    pending = load(PENDING, {"suggestions": {}})
    pending.setdefault("suggestions", {})
    opened = 0
    for key, s in found.items():
        if key in pending["suggestions"] or (results.get(s["office"]) or {}).get(s["state"]):
            continue
        if opened >= MAX_NEW:
            break
        title, body = issue_text(s)
        if not (TOKEN and REPO):
            print(f"(dry run, no GitHub token) would open: {title}")
            continue
        try:
            issue = gh("POST", f"/repos/{REPO}/issues", {"title": title, "body": body})
        except urllib.error.URLError as exc:
            warn(f"Could not open an issue for {key}: {exc}")
            continue
        pending["suggestions"][key] = dict(s, issue=issue.get("number"), status="open", opened=now_iso())
        opened += 1
        print(f"Opened issue #{issue.get('number')}: {title}")
    if opened:
        save(PENDING, pending)


def sync():
    pending = load(PENDING, {"suggestions": {}})
    waiting = {k: v for k, v in pending.get("suggestions", {}).items() if v.get("status") == "open" and v.get("issue")}
    if not waiting:
        print("No suggestions waiting for confirmation.")
        return
    if not (TOKEN and REPO):
        print("No GitHub token; skipping the sync.")
        return
    results = load(RESULTS, {})
    for k in ("cases", "senate", "governors"):
        if not isinstance(results.get(k), dict):
            results[k] = {}
    changed = False
    for key, s in waiting.items():
        try:
            issue = gh("GET", f"/repos/{REPO}/issues/{s['issue']}")
        except urllib.error.URLError as exc:
            warn(f"Could not read issue #{s['issue']}: {exc}")
            continue
        if issue.get("state") != "closed":
            continue
        m = MARK.search(issue.get("body") or "")
        meta = json.loads(m.group(1)) if m else s
        if issue.get("state_reason") in ("completed", None):
            entry = {"winner": meta["winner"]}
            if meta.get("source"):
                entry["source"] = meta["source"]
            if meta.get("url"):
                entry["url"] = meta["url"]
            results[meta["office"]][meta["state"]] = entry
            s["status"] = "confirmed"
            note = "Listo: el resultado ya está cargado en el sitio. Para deshacerlo, usá Run workflow con kind senate o governors, el estado en key y clear en value."
            print(f"Confirmed {key} (issue #{s['issue']})")
        else:
            s["status"] = "rejected"
            note = "Descartado: el robot no va a volver a sugerir este resultado."
            print(f"Rejected {key} (issue #{s['issue']})")
        s["closed"] = now_iso()
        changed = True
        try:
            gh("POST", f"/repos/{REPO}/issues/{s['issue']}/comments", {"body": note})
        except urllib.error.URLError:
            pass
    if changed:
        results["updated_at"] = now_iso()
        save(RESULTS, results)
        save(PENDING, pending)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "suggest":
        suggest(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "sync":
        sync()
    else:
        print(__doc__)
        sys.exit(2)
