from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
load_dotenv(_root / ".env", override=False)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from src.graph import llm as llm_mod
from src.graph.config import CHAT_FORMAT
from src.graph.nodes import plan as plan_mod
from src.graph.nodes import reason as reason_mod
from src.graph.prompts import REASON_SYSTEM

APPS = ["Notes", "Safari", "Calculator", "Spotify", "Mail", "Calendar", "Finder", "Terminal"]

ROUTER_CASES = [
    ("hey, how's it going?", "chat"),
    ("what's the capital of France?", "chat"),
    ("tell me a joke", "chat"),
    ("what can you do?", "chat"),
    ("open the calculator", "automation"),
    ("open notes and write a poem about the sea", "automation"),
    ("search google for the weather in kuala lumpur", "automation"),
    ("send an email to sam saying I'll be late", "automation"),
    ("turn the volume down", "automation"),
    ("what's on my calendar tomorrow", "automation"),
]


def _state(**kw) -> dict:
    base = {
        "goal": "", "plan": [], "plan_step": 0, "history": [], "installed_apps": APPS,
        "session_context": "", "current_url": "", "page_title": "", "page_text": "",
        "screen_elements": "", "shortcuts_raw": "", "on_message": None, "user_answer": "",
    }
    base.update(kw)
    return base


NOTES_ELEMENTS = '[1] button: "New Note"\n[2] text field: "Search"\n[3] static: "All iCloud"'
GOOGLE_ELEMENTS = '[1] <input> "Search" (combobox)\n[2] <button> "Google Search"\n[3] <a> "Gmail" href=https://mail.google.com'

REASON_CASES = [
    ("open_app_notes", _state(goal="Open Notes and write a poem", plan=["Open Notes", "Create a new note", "Write a poem"]),
     lambda d: d.get("action") == "open_app" and "notes" in d.get("name", "").lower(), "open_app Notes"),
    ("open_app_calc", _state(goal="open the calculator", plan=["Open Calculator"]),
     lambda d: d.get("action") == "open_app" and "calc" in d.get("name", "").lower(), "open_app Calculator"),
    ("new_note_click", _state(goal="Open Notes and write a poem", plan=["Open Notes", "Create a new note", "Write a poem"], plan_step=1,
                              screen_elements=NOTES_ELEMENTS,
                              history=[{"action": "open_app", "target": "Notes", "result": "Opened Notes"}]),
     lambda d: (d.get("action") == "click" and ("1" in str(d.get("target", d.get("selector", ""))))) or
     (d.get("action") == "key_combo" and "n" == str(d.get("combo", "")).lower().split("+")[-1].strip()), "click [1] or cmd+n"),
    ("type_poem", _state(goal="Open Notes and write a poem", plan=["Open Notes", "Create a new note", "Write a poem"], plan_step=2,
                         screen_elements='[1] text area: "Note body"',
                         history=[{"action": "open_app", "target": "Notes", "result": "Opened Notes"},
                                  {"action": "key_combo", "target": "cmd+n", "result": "Pressed cmd+n"}]),
     lambda d: (d.get("action") == "type_text" and len(d.get("text", "")) > 20 and "content here" not in d.get("text", "").lower())
     or (d.get("action") == "write_content" and "poem" in d.get("description", "").lower()), "type_text or write_content poem"),
    ("google_nav", _state(goal="search google for the weather in kuala lumpur", plan=["Search Google for the weather"]),
     lambda d: (d.get("action") == "web_search" and "kuala" in d.get("query", "").lower())
     or (d.get("action") == "navigate" and "google" in d.get("url", "") and "kuala" in d.get("url", "").lower()), "web_search or navigate weather"),
    ("type_in_search", _state(goal="search for cats on google", plan=["Search for cats"], current_url="https://www.google.com", page_title="Google",
                              screen_elements=GOOGLE_ELEMENTS),
     lambda d: d.get("action") == "type_text" and "cat" in d.get("text", "").lower(), "type_text 'cats'"),
    ("submit_search", _state(goal="search for cats on google", plan=["Search for cats"], current_url="https://www.google.com", page_title="Google",
                             screen_elements=GOOGLE_ELEMENTS,
                             history=[{"action": "type_text", "target": "cats", "result": "Typed cats"}]),
     lambda d: d.get("action") == "key_combo" and "return" in d.get("combo", "").lower() or "enter" in d.get("combo", "").lower(), "key_combo return"),
    ("finish_when_done", _state(goal="open the calculator", plan=["Open Calculator"],
                                history=[{"action": "open_app", "target": "Calculator", "result": "Opened Calculator"}]),
     lambda d: d.get("action") == "done", "done"),
    ("open_step_not_app", _state(goal="login to portal and open fees summary", plan=["Open Fees Summary"], current_url="https://portal.example.com", page_title="Portal",
                                 screen_elements='[1] <a> "Fees Summary" href=/fees\n[2] <a> "Profile" href=/me'),
     lambda d: d.get("action") == "click", "click Fees Summary (not open_app)"),
]


def _count_tokens(text: str) -> int:
    return len(llm_mod._get_llm().tokenize(text.encode(), add_bos=False))


def _timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000


def run_router() -> list[dict]:
    rows = []
    for goal, expect in ROUTER_CASES:
        prompt = plan_mod._build_plan_prompt(_state(goal=goal), goal)
        (mode, steps, reply), ms = _timed(lambda: plan_mod._generate_plan_or_chat(prompt))
        rows.append({"case": goal, "expect": expect, "got": mode, "ok": mode == expect, "ms": ms,
                     "detail": reply or steps, "prompt_tokens": _count_tokens(plan_mod.PLAN_SYSTEM + prompt)})
    return rows


def run_reason() -> list[dict]:
    rows = []
    for name, st, pred, desc in REASON_CASES:
        plan = st["plan"]
        step = plan[st["plan_step"]] if plan else ""
        hist_block, failed = reason_mod._build_history_summary(st["history"])
        is_native = not st["current_url"]
        elements = reason_mod._prepare_element_list(st["screen_elements"], is_native, step, st["goal"])
        user = reason_mod._assemble_prompt(st, step, is_native, hist_block, elements, failed)
        grammar = reason_mod.grammar_for_step(step, st["installed_apps"])

        def _call():
            return llm_mod.call_llm(REASON_SYSTEM, user, num_predict=256, grammar=grammar)

        raw, ms = _timed(_call)
        decision = llm_mod.extract_json(raw)
        rows.append({"case": name, "expect": desc, "got": raw[:110], "ok": bool(pred(decision)), "ms": ms,
                     "prompt_tokens": _count_tokens(REASON_SYSTEM + user)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["router", "reason"])
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    print(f"chat_format={CHAT_FORMAT} model={os.getenv('LOKI_MODEL_PATH')}")
    llm_mod._get_llm()
    report = {"chat_format": CHAT_FORMAT}
    for name, fn in (("router", run_router), ("reason", run_reason)):
        if args.only and args.only != name:
            continue
        llm_mod.reset_kv_cache()
        rows = fn()
        report[name] = rows
        ok = sum(r["ok"] for r in rows)
        avg_ms = sum(r["ms"] for r in rows) / len(rows)
        avg_tok = sum(r["prompt_tokens"] for r in rows) / len(rows)
        print(f"\n== {name}: {ok}/{len(rows)} correct | avg {avg_ms:.0f} ms/call | avg {avg_tok:.0f} prompt tokens")
        for r in rows:
            print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['case'][:40]:40} {r['ms']:6.0f}ms  want={r['expect']}  got={str(r['got'])[:100]}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
