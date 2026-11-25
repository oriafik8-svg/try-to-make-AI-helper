import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.brain import Brain, BrainError  # noqa: E402
from ai.config import Config  # noqa: E402
from ai.mcp_server import handle  # noqa: E402
from ai.tools import TimerStore, build_tools, calculator  # noqa: E402


def make_tools(tmp, **kw):
    cfg = Config()
    store = TimerStore(Path(tmp) / "timers.json")
    tools = {t.name: t for t in build_tools(cfg, timers=store, **kw)}
    return cfg, store, tools


class CalculatorTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(calculator("12*(3+4)"), "84")
        self.assertEqual(calculator("2^10"), "1024")
        self.assertEqual(calculator("sqrt(144)"), "12")
        self.assertEqual(calculator("10/4"), "2.5")

    def test_errors_and_safety(self):
        self.assertIn("חילוק באפס", calculator("1/0"))
        self.assertIn("שגיאה", calculator("__import__('os').system('id')"))
        self.assertIn("שגיאה", calculator("9**999"))
        self.assertIn("שגיאה", calculator("x"))


class TimerTests(unittest.TestCase):
    def test_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, store, tools = make_tools(tmp)
            self.assertIn("נקבע", tools["set_timer"].fn(seconds=60, label="פסטה"))
            self.assertIn("פסטה", tools["list_timers"].fn())
            self.assertEqual(store.pop_due(), [])
            due = store.pop_due(now=time.time() + 61)
            self.assertEqual([t["label"] for t in due], ["פסטה"])
            self.assertEqual(store.pop_due(now=time.time() + 120), [])  # לא מצפצף פעמיים
            self.assertEqual(tools["list_timers"].fn(), "אין טיימרים פעילים")

    def test_cancel_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, tools = make_tools(tmp)
            tools["set_timer"].fn(seconds=30)
            self.assertEqual(tools["cancel_timer"].fn(timer_id=1), "הטיימר בוטל")
            self.assertEqual(tools["cancel_timer"].fn(timer_id=1), "לא נמצא טיימר כזה")
            self.assertIn("שגיאה", tools["set_timer"].fn(seconds=0))
            self.assertIn("שגיאה", tools["set_timer"].fn(seconds=10**6))


class WeatherAndHomeTests(unittest.TestCase):
    def test_weather(self):
        fake = lambda url: {"current": {"temperature_2m": 21.5, "apparent_temperature": 20.9,  # noqa: E731
                                        "relative_humidity_2m": 55, "weather_code": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            _, _, tools = make_tools(tmp, http_get=fake)
            self.assertIn("21.5", tools["outside_temperature"].fn())

    def test_weather_failure_is_text(self):
        def boom(url):
            raise OSError("no network")
        with tempfile.TemporaryDirectory() as tmp:
            _, _, tools = make_tools(tmp, http_get=boom)
            self.assertIn("שגיאה", tools["outside_temperature"].fn())

    def test_home_confirmation_gate(self):
        calls = []
        def fake(method, path, body):
            calls.append((method, path, body))
            return {}
        with tempfile.TemporaryDirectory() as tmp:
            _, _, tools = make_tools(tmp, http_call=fake)
            fn = tools["home_call_service"].fn
            self.assertIn("אישור", fn(domain="lock", service="unlock", entity_id="lock.front"))
            self.assertEqual(calls, [])  # לא בוצע בלי confirm
            fn(domain="lock", service="unlock", entity_id="lock.front", confirm=True)
            self.assertEqual(len(calls), 1)
            fn(domain="light", service="turn_on", entity_id="light.salon")
            self.assertEqual(calls[-1][1], "/api/services/light/turn_on")


class McpTests(unittest.TestCase):
    def test_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, tools = make_tools(tmp)
            init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26"}}, tools)
            self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
            self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, tools))
            listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tools)
            names = {t["name"] for t in listed["result"]["tools"]}
            self.assertIn("calculator", names)
            res = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "calculator", "arguments": {"expression": "6*7"}}}, tools)
            self.assertEqual(res["result"]["content"][0]["text"], "42")
            bad = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "calculator", "arguments": {"nope": 1}}}, tools)
            self.assertTrue(bad["result"]["isError"])
            self.assertIn("error", handle({"jsonrpc": "2.0", "id": 5, "method": "x"}, tools))

    def test_real_subprocess_roundtrip(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "calculator", "arguments": {"expression": "2+2"}}},
        ]
        proc = subprocess.run(
            [sys.executable, "-m", "ai.mcp_server"], cwd=ROOT, text=True, capture_output=True,
            input="\n".join(json.dumps(m) for m in msgs) + "\n", timeout=20,
        )
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[1]["result"]["content"][0]["text"], "4")


class BrainTests(unittest.TestCase):
    def fake_runner(self, payload, code=0, seen=None):
        def run(cmd, **kw):
            if seen is not None:
                seen.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, code, json.dumps(payload), "")
        return run

    def test_command_and_session(self):
        seen = []
        brain = Brain(Config(), runner=self.fake_runner(
            {"result": "שלום", "session_id": "abc", "is_error": False}, seen=seen))
        self.assertEqual(brain.ask("היי"), "שלום")
        cmd, kw = seen[0]
        self.assertEqual(kw["input"], "היי")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--resume", cmd)
        brain.ask("עוד")
        self.assertIn("abc", seen[1][0])

    def test_auth_error_is_clear(self):
        brain = Brain(Config(), runner=self.fake_runner(
            {"result": "API Error: 401 Invalid bearer token", "is_error": True}, code=1))
        with self.assertRaises(BrainError) as ctx:
            brain.ask("היי")
        self.assertIn("/login", str(ctx.exception))

    def test_missing_binary(self):
        def run(*a, **k):
            raise FileNotFoundError
        with self.assertRaises(BrainError):
            Brain(Config(), runner=run).ask("היי")


if __name__ == "__main__":
    unittest.main()
