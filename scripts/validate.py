#!/usr/bin/env python3
"""Check the files in data/ before publishing, so a typo never breaks the site silently."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors = []


def load(name):
    path = ROOT / "data" / name
    if not path.exists():
        print(f"{name}: missing (optional)")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{name}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
        return None


news = load("news.json")
if news is not None:
    stories = news.get("stories")
    if not isinstance(stories, list):
        errors.append("news.json: 'stories' must be a list")
    else:
        for i, s in enumerate(stories):
            for field in ("title", "url"):
                if not isinstance(s.get(field), str) or not s.get(field):
                    errors.append(f"news.json: story {i + 1} needs a '{field}'")
            if s.get("signal") not in (None, 1, 2, 3):
                errors.append(f"news.json: story {i + 1} has a signal outside 1-3")
        print(f"news.json: {len(stories)} stories, updated {news.get('updated_at')}")

election = load("election.json")
if election is not None:
    for chamber, keys in (("house", ("republican", "democratic", "independent", "vacant")), ("senate", ("republican", "democratic", "independent"))):
        block = election.get(chamber)
        if not isinstance(block, dict) or not all(isinstance(block.get(k, 0), int) for k in keys):
            errors.append(f"election.json: '{chamber}' needs whole-number counts for {', '.join(keys)}")
    if isinstance(election.get("house"), dict):
        total = sum(int(election["house"].get(k, 0)) for k in ("republican", "democratic", "independent", "vacant"))
        if total != 435:
            errors.append(f"election.json: House seats add up to {total}, not 435")
    if isinstance(election.get("senate"), dict):
        total = sum(int(election["senate"].get(k, 0)) for k in ("republican", "democratic", "independent"))
        if total != 100:
            errors.append(f"election.json: Senate seats add up to {total}, not 100")
    print(f"election.json: phase {election.get('phase')}, updated {election.get('updated_at')}")

results = load("results.json")
if results is not None:
    for office in ("senate", "governors"):
        for st, v in (results.get(office) or {}).items():
            w = v.get("winner") if isinstance(v, dict) else v
            if str(w).upper() not in ("D", "R", "I"):
                errors.append(f"results.json: {office}.{st} must be D, R or I (got {w!r})")
    for cid, v in (results.get("cases") or {}).items():
        if not isinstance(v, dict) or not v.get("result"):
            errors.append(f"results.json: cases.{cid} needs a 'result' text")
    h = results.get("house_net_change_presidents_party")
    if h is not None and not isinstance(h, int):
        errors.append("results.json: house_net_change_presidents_party must be a whole number or null")
    print(f"results.json: {len(results.get('senate') or {})} Senate, {len(results.get('governors') or {})} governor and {len(results.get('cases') or {})} case results")

if errors:
    for e in errors:
        print(f"::error::{e}")
    sys.exit(1)
print("All data files are valid.")
