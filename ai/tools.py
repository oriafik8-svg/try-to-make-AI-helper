"""הכלים של Ai. כל כלי: שם, תיאור, סכמה, פונקציה שמחזירה טקסט.

עקרונות:
- הכלים רצים בתהליך נפרד (שרת MCP) שנוצר מחדש בכל קריאה ל-claude -p,
  לכן כל מצב מתמשך (טיימרים) נשמר בקובץ ולא בזיכרון.
- פעולות פיזיות רגישות דורשות confirm=true (הגנה מפני prompt injection).
"""
from __future__ import annotations

import ast
import json
import math
import operator
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .config import STATE_DIR, Config


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    fn: Callable[..., str]


# ---------- מחשבון (בלי eval) ----------
_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e}


def safe_eval(expr: str) -> float:
    if len(expr) > 200:
        raise ValueError("הביטוי ארוך מדי")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("חזקה גדולה מדי")
            return _BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return _UN[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _CONSTS:
            return _CONSTS[node.id]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FUNCS and not node.keywords):
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("ביטוי לא נתמך")

    return ev(ast.parse(expr.replace("^", "**").strip(), mode="eval"))


def calculator(expression: str) -> str:
    try:
        result = safe_eval(expression)
    except ZeroDivisionError:
        return "שגיאה: חילוק באפס"
    except (ValueError, SyntaxError, OverflowError) as exc:
        return f"שגיאה: {exc}"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(round(result, 10) if isinstance(result, float) else result)


# ---------- טיימרים (נשמרים בקובץ; מי שמצפצף הוא watcher בתהליך הראשי) ----------
class TimerStore:
    def __init__(self, path=None):
        self.path = path or STATE_DIR / "timers.json"

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write(self, timers: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(timers, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, seconds: float, label: str) -> dict:
        timers = self._read()
        next_id = max([t["id"] for t in timers], default=0) + 1
        timer = {"id": next_id, "label": label, "due": time.time() + seconds, "fired": False}
        timers.append(timer)
        self._write(timers)
        return timer

    def active(self) -> list[dict]:
        return [t for t in self._read() if not t["fired"]]

    def cancel(self, timer_id: int) -> bool:
        timers = self._read()
        kept = [t for t in timers if t["id"] != timer_id]
        self._write(kept)
        return len(kept) != len(timers)

    def pop_due(self, now: float | None = None) -> list[dict]:
        """מחזיר טיימרים שהגיע זמנם ומסמן אותם. נקרא רק מה-watcher."""
        now = now or time.time()
        timers = self._read()
        due = [t for t in timers if not t["fired"] and t["due"] <= now]
        if due:
            for t in due:
                t["fired"] = True
            self._write(timers)
        return due


def beep(beep_command: list[str]) -> None:
    if beep_command:
        subprocess.run(beep_command, check=False, timeout=15)
    else:
        print("\a", end="", flush=True)


def _fmt_remaining(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} שעות")
    if m:
        parts.append(f"{m} דקות")
    if s or not parts:
        parts.append(f"{s} שניות")
    return " ו".join(parts)


# ---------- בניית רשימת הכלים ----------
def build_tools(cfg: Config, *, timers: TimerStore | None = None,
                http_get: Callable[[str], dict] | None = None,
                http_call: Callable[[str, str, dict], dict] | None = None) -> list[Tool]:
    timers = timers or TimerStore()
    tz = ZoneInfo(cfg.timezone)

    def get_json(url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "ai-home-assistant"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def ha_request(method: str, path: str, body: dict | None = None) -> dict:
        token = cfg.ha_token
        if not token:
            raise RuntimeError("חסר HA_TOKEN במשתני הסביבה")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            cfg.ha_url.rstrip("/") + path, data=data, method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")

    fetch = http_get or get_json
    ha_call = http_call or ha_request

    def current_time() -> str:
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S %A") + f" ({cfg.timezone})"

    def set_timer(seconds: float, label: str = "טיימר") -> str:
        if not 1 <= seconds <= 24 * 3600:
            return "שגיאה: משך הטיימר חייב להיות בין שנייה ל-24 שעות"
        t = timers.add(seconds, label)
        return f"טיימר {t['id']} ({label}) נקבע ל-{_fmt_remaining(seconds)}"

    def list_timers() -> str:
        active = timers.active()
        if not active:
            return "אין טיימרים פעילים"
        return "\n".join(
            f"{t['id']}: {t['label']} - נותרו {_fmt_remaining(t['due'] - time.time())}"
            for t in active
        )

    def cancel_timer(timer_id: int) -> str:
        return "הטיימר בוטל" if timers.cancel(timer_id) else "לא נמצא טיימר כזה"

    def outside_temperature() -> str:
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={cfg.latitude}&longitude={cfg.longitude}"
               "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code"
               f"&timezone={cfg.timezone}")
        try:
            cur = fetch(url)["current"]
        except (urllib.error.URLError, KeyError, TimeoutError, OSError) as exc:
            return f"שגיאה בקבלת מזג אוויר: {exc}"
        return (f"בחוץ {cur['temperature_2m']} מעלות "
                f"(מרגיש כמו {cur['apparent_temperature']}), "
                f"לחות {cur['relative_humidity_2m']}%, קוד מזג אוויר {cur['weather_code']}")

    def ha_get_state(entity_id: str) -> str:
        try:
            st = ha_call("GET", f"/api/states/{entity_id}", None)
        except Exception as exc:  # noqa: BLE001 - מחזירים טקסט למודל
            return f"שגיאה מ-Home Assistant: {exc}"
        name = st.get("attributes", {}).get("friendly_name", entity_id)
        return f"{name}: {st.get('state')}"

    def ha_call_service(domain: str, service: str, entity_id: str,
                        confirm: bool = False) -> str:
        if domain in cfg.ha_confirm_domains and not confirm:
            return (f"נדרש אישור מפורש מהמשתמש לפני {domain}.{service} על {entity_id}. "
                    "שאל אותו, ורק אם אישר בקולו קרא שוב עם confirm=true.")
        try:
            ha_call("POST", f"/api/services/{domain}/{service}", {"entity_id": entity_id})
        except Exception as exc:  # noqa: BLE001
            return f"שגיאה מ-Home Assistant: {exc}"
        return f"בוצע: {domain}.{service} על {entity_id}"

    def get_preferences() -> str:
        return json.dumps({
            "messaging_provider": cfg.messaging_provider,
            "music_provider": cfg.music_provider,
            "note": "שליחת הודעות ומוזיקה עוד לא מחוברות בשלב 1",
        }, ensure_ascii=False)

    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    return [
        Tool("calculator", "מחשבון. מקבל ביטוי מתמטי, למשל 12*(3+4) או sqrt(144).",
             obj({"expression": {"type": "string"}}, ["expression"]), calculator),
        Tool("current_time", "התאריך והשעה הנוכחיים.", obj({}), current_time),
        Tool("set_timer", "קובע טיימר שיצפצף כשיסתיים.",
             obj({"seconds": {"type": "number"}, "label": {"type": "string"}}, ["seconds"]),
             set_timer),
        Tool("list_timers", "מציג טיימרים פעילים וכמה זמן נותר.", obj({}), list_timers),
        Tool("cancel_timer", "מבטל טיימר לפי מספר.",
             obj({"timer_id": {"type": "integer"}}, ["timer_id"]), cancel_timer),
        Tool("outside_temperature", "הטמפרטורה ומזג האוויר בחוץ עכשיו.", obj({}),
             outside_temperature),
        Tool("home_get_state", "מצב של מכשיר ב-Home Assistant (entity_id).",
             obj({"entity_id": {"type": "string"}}, ["entity_id"]), ha_get_state),
        Tool("home_call_service",
             "מפעיל שירות ב-Home Assistant, למשל light.turn_on. פעולות על "
             "מנעולים, אזעקה, תריסים ודוד דורשות confirm=true אחרי אישור מהמשתמש.",
             obj({"domain": {"type": "string"}, "service": {"type": "string"},
                  "entity_id": {"type": "string"}, "confirm": {"type": "boolean"}},
                 ["domain", "service", "entity_id"]), ha_call_service),
        Tool("get_preferences", "העדפות המשתמש: וואטסאפ או אפליקציה, Spotify או YouTube Music.",
             obj({}), get_preferences),
    ]
