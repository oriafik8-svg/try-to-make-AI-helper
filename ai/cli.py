"""שורת פקודה: python -m ai chat | tools | call <tool> '{json}'"""
from __future__ import annotations

import argparse
import json
import threading

from .brain import Brain, BrainError
from .config import load_config
from .tools import TimerStore, beep, build_tools


def timer_watcher(cfg, store: TimerStore, stop: threading.Event) -> None:
    while not stop.wait(0.5):
        for t in store.pop_due():
            print(f"\n[טיימר] {t['label']} הסתיים")
            beep(cfg.beep_command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat", help="שיחה בטקסט (בלי מיקרופון)")
    sub.add_parser("tools", help="רשימת הכלים")
    call = sub.add_parser("call", help="הרצת כלי ישירות, לבדיקה")
    call.add_argument("name")
    call.add_argument("args", nargs="?", default="{}")
    args = parser.parse_args(argv)

    cfg = load_config()
    tools = {t.name: t for t in build_tools(cfg)}

    if args.cmd == "tools":
        for t in tools.values():
            print(f"{t.name}: {t.description}")
        return 0
    if args.cmd == "call":
        if args.name not in tools:
            print("כלי לא קיים")
            return 1
        print(tools[args.name].fn(**json.loads(args.args)))
        return 0

    store, stop = TimerStore(), threading.Event()
    threading.Thread(target=timer_watcher, args=(cfg, store, stop), daemon=True).start()
    brain = Brain(cfg)
    print("Ai מוכן. כתוב הודעה (Ctrl+C ליציאה).")
    try:
        while True:
            text = input("> ").strip()
            if not text:
                continue
            try:
                print(brain.ask(text))
            except BrainError as exc:
                print(f"[שגיאה] {exc}")
    except (KeyboardInterrupt, EOFError):
        stop.set()
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
