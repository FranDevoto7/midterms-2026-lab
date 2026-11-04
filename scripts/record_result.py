#!/usr/bin/env python3
"""Record one result in data/results.json (used by the "Run workflow" form on GitHub).

Examples:
  python scripts/record_result.py --kind senate --key GA --value D --source AP --url https://...
  python scripts/record_result.py --kind governors --key AZ --value clear
  python scripts/record_result.py --kind case --key maine --value "Collins wins by 3 points" --source AP
  python scripts/record_result.py --kind house_net_change --value -12
"""
import argparse
import re
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "results.json"
META = json.loads((ROOT / "scripts" / "lab_meta.json").read_text(encoding="utf-8"))


def fail(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["senate", "governors", "case", "house_net_change"])
    ap.add_argument("--key", default="")
    ap.add_argument("--value", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--note", default="", help="short description shown with the result on the site")
    a = ap.parse_args()

    data = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    for k, empty in (("cases", {}), ("senate", {}), ("governors", {})):
        if not isinstance(data.get(k), dict):
            data[k] = dict(empty)
    data.setdefault("house_net_change_presidents_party", None)
    key, value = a.key.strip(), a.value.strip()
    source, url, note = a.source.strip(), a.url.strip(), a.note.strip()[:600]
    # "Hito: texto | descripción" — the part after "Hito:" is the milestone, the rest the description.
    milestone = ""
    m = re.match(r"^\s*hito\s*[:\-–—]\s*(.*)$", note, re.I | re.S)
    if m:
        head, _, tail = m.group(1).partition(" | ")
        milestone, note = head.strip()[:300], tail.strip()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if url and not url.startswith(("http://", "https://")):
        fail("The url must start with http:// or https://.")

    if a.kind in ("senate", "governors"):
        key = key.upper()
        valid = META["senate_up"] if a.kind == "senate" else META["governors_up"]
        if key not in valid:
            fail(f"{key or '(empty)'} has no {a.kind} race in 2026. Valid codes: {', '.join(valid)}")
        winner = value.upper()
        if winner == "CLEAR":
            data[a.kind].pop(key, None)
            data.setdefault("cleared", {})[f"{a.kind}:{key}"] = now  # the robot will not put it back
            print(f"Cleared {a.kind} result for {key}.")
        elif winner in ("D", "R", "I"):
            old = data[a.kind].get(key) if isinstance(data[a.kind].get(key), dict) else {}
            same = old.get("winner") == winner
            entry = {"winner": winner}
            if source or (same and old.get("source")):
                entry["source"] = source or old["source"]
            if url or (same and old.get("url")):
                entry["url"] = url or old["url"]
            if note:
                entry["note"], entry["note_from"] = note, "form"
            elif same and old.get("note"):
                entry["note"] = old["note"]
                if old.get("note_from"):
                    entry["note_from"] = old["note_from"]
            if milestone.lower() in ("borrar", "quitar", "clear", "-"):
                entry["milestone_from"] = "form"  # "Hito: borrar" removes the milestone, and the robot won't put it back
            elif milestone:
                entry["milestone"], entry["milestone_from"] = milestone, "form"
            elif same and old.get("milestone"):
                entry["milestone"] = old["milestone"]
                if old.get("milestone_url"):
                    entry["milestone_url"] = old["milestone_url"]
            entry["at"] = old.get("at") if same and old.get("at") else now
            data[a.kind][key] = entry
            (data.get("cleared") or {}).pop(f"{a.kind}:{key}", None)
            print(f"{a.kind} {key}: {winner}")
        else:
            fail("For senate and governors, value must be D, R, I or clear.")
    elif a.kind == "case":
        if key not in META["case_ids"]:
            fail(f"Unknown case id {key or '(empty)'}. Valid ids: {', '.join(META['case_ids'])}")
        if value.lower() == "clear":
            data["cases"].pop(key, None)
            print(f"Cleared case result for {key}.")
        elif value:
            entry = {"result": value}
            if source:
                entry["source"] = source
            if url:
                entry["url"] = url
            old = data["cases"].get(key) if isinstance(data["cases"].get(key), dict) else {}
            if note:
                entry["note"] = note
            elif old.get("note"):
                entry["note"] = old["note"]
            entry["at"] = old.get("at") or now
            data["cases"][key] = entry
            print(f"case {key}: {value}")
        else:
            fail("Write the result text in value (or clear).")
    else:
        if value.lower() in ("clear", ""):
            data["house_net_change_presidents_party"] = None
            data.pop("house_from", None)
            data.pop("house_auto", None)
            print("Cleared the House net change.")
        else:
            v = value.replace("−", "-").strip()
            net = called = None
            # "R 180 D 240": seats won by each party, as results pages show them; the change is R seats minus the 220 of 2024.
            mr, md = re.search(r"\bR\s*[:=]?\s*(\d{1,3})\b", v, re.I), re.search(r"\bD\s*[:=]?\s*(\d{1,3})\b", v, re.I)
            if mr:
                net = int(mr.group(1)) - 220
                called = int(mr.group(1)) + (int(md.group(1)) if md else 0) if md else None
            else:
                # "-27" or "-27 de 425": the net change, and optionally how many seats are decided.
                m = re.match(r"^([+-]?\d{1,3})(?:\s*(?:de|of|/)\s*(\d{1,3}))?$", v, re.I)
                if m:
                    net, called = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
            if net is None or abs(net) > 150 or (called is not None and not 0 < called <= 435):
                fail("For house_net_change, write the net change (-27), the net change and seats decided (-27 de 425), or the seats of each party (R 180 D 240).")
            data["house_net_change_presidents_party"] = net
            data["house_from"] = "form"
            data["house_auto"] = {"source": source or "", "url": url or "", "called": called, "at": now, "manual": True}
            print(f"House net change for the president's party: {data['house_net_change_presidents_party']}")

    data["updated_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
