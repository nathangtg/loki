from __future__ import annotations

import argparse
import json
import time

from src.graph.llm import call_llm, extract_json
from src.graph.prompts import REASON_SYSTEM
from src.graph.grammars import ACTION_GRAMMAR, load_grammar

_SCENARIOS = [
    (
        "type_text (search box)",
        "GOAL: Search Google for the weather in Kuala Lumpur\n"
        "CURRENT STEP: Search for weather\n\nACTIONS SO FAR:\n  (none)\n\n"
        "URL: https://www.google.com/\nTitle: Google\n\n"
        "INTERACTIVE ELEMENTS:\n"
        "[0] textarea \"Search\"\n"
        "[1] input:submit \"Google Search\"\n\n"
        "Next JSON action:",
        lambda p: p.get("action") == "type_text",
    ),
    (
        "click (nav link)",
        "GOAL: Check my timetable\nCURRENT STEP: Navigate to timetable\n\n"
        "ACTIONS SO FAR:\n  1. login → OK\n\n"
        "URL: https://izone.sunway.edu.my/dashboard\nTitle: Dashboard\n\n"
        "INTERACTIVE ELEMENTS:\n"
        "[0] a \"Dashboard\"\n[1] a \"My Profile\"\n[2] a \"Finance\"\n"
        "[3] a \"My Timetable\"\n[4] a \"Exam Results\"\n[5] button \"Logout\"\n\n"
        "Next JSON action:",
        lambda p: p.get("action") == "click" and "3" in str(p.get("target", "")),
    ),
    (
        "open_app (native)",
        "GOAL: Open Spotify\nCURRENT STEP: Open Spotify\n\n"
        "ACTIONS SO FAR:\n  (none)\n\nURL: (no browser open)\nTitle: (none)\n\n"
        "INTERACTIVE ELEMENTS:\n(none)\n\nNext JSON action:",
        lambda p: p.get("action") == "open_app",
    ),
    (
        "done (task complete)",
        "GOAL: Find the capital of Malaysia\nCURRENT STEP: Read results\n\n"
        "ACTIONS SO FAR:\n  1. navigate google search → OK\n\n"
        "URL: https://www.google.com/search?q=capital+of+malaysia\nTitle: Google Search\n\n"
        "PAGE TEXT:\nKuala Lumpur\nCapital of Malaysia\n\n"
        "INTERACTIVE ELEMENTS:\n[0] textarea \"capital of malaysia\"\n\n"
        "Next JSON action:",
        lambda p: p.get("action") in ("done", "read_page"),
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="model", help="Display name for this run")
    parser.add_argument("--runs", type=int, default=2, help="Repeats per scenario (averages latency)")
    parser.add_argument(
        "--backend",
        choices=["llamacpp", "mlx"],
        default=None,
        help="Override LOKI_LLM_BACKEND ('llamacpp' or 'mlx')",
    )
    args = parser.parse_args()

    if args.backend:
        import os
        import src.graph.config as cfg
        import src.graph.llm as llm_mod

        os.environ["LOKI_LLM_BACKEND"] = args.backend
        cfg.LLM_BACKEND = args.backend
        llm_mod.LLM_BACKEND = args.backend

    grammar = load_grammar(ACTION_GRAMMAR)

    print(f"\n{'='*70}\n  Benchmark: {args.label}\n{'='*70}")

    total_correct = 0
    total_time = 0.0
    total_runs = 0

    for name, prompt, validate in _SCENARIOS:
        times = []
        correct = 0
        last_decision = {}
        for _ in range(args.runs):
            t0 = time.time()
            raw = call_llm(REASON_SYSTEM, prompt, num_predict=256, temperature=0.0, grammar=grammar)
            dt = time.time() - t0
            times.append(dt)
            decision = extract_json(raw)
            last_decision = decision
            if decision and validate(decision):
                correct += 1

        avg_time = sum(times) / len(times)
        total_correct += correct
        total_time += sum(times)
        total_runs += len(times)

        status = "PASS" if correct == args.runs else ("PARTIAL" if correct else "FAIL")
        print(f"  [{status:7s}] {name:28s} avg={avg_time:.2f}s  correct={correct}/{args.runs}  last={json.dumps(last_decision)[:80]}")

    print(f"\n  Overall: {total_correct}/{total_runs} correct, avg latency {total_time/total_runs:.2f}s/call")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
