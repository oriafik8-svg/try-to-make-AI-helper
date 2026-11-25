"""שרת MCP מינימלי (stdio, JSON-RPC שורה-שורה) שחושף את הכלים ל-claude -p.

הפעלה: python -m ai.mcp_server  (claude מפעיל אותו לבד דרך --mcp-config)
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .config import load_config
from .tools import Tool, build_tools

SERVER_INFO = {"name": "ai-tools", "version": "0.1.0"}


def handle(msg: dict[str, Any], tools: dict[str, Tool]) -> dict[str, Any] | None:
    """מטפל בהודעה אחת. מחזיר תשובה, או None להודעות בלי תשובה (notifications)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    if msg_id is None:  # notification
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        return ok({"protocolVersion": version, "capabilities": {"tools": {}},
                   "serverInfo": SERVER_INFO})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.schema}
            for t in tools.values()
        ]})
    if method == "tools/call":
        params = msg.get("params") or {}
        tool = tools.get(params.get("name", ""))
        if tool is None:
            return err(-32602, "כלי לא קיים")
        try:
            text = tool.fn(**(params.get("arguments") or {}))
            return ok({"content": [{"type": "text", "text": str(text)}], "isError": False})
        except TypeError as exc:
            return ok({"content": [{"type": "text", "text": f"פרמטרים לא תקינים: {exc}"}],
                       "isError": True})
        except Exception as exc:  # noqa: BLE001 - שגיאת כלי לא מפילה את השרת
            return ok({"content": [{"type": "text", "text": f"שגיאה: {exc}"}], "isError": True})
    return err(-32601, f"שיטה לא מוכרת: {method}")


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    tools = {t.name: t for t in build_tools(load_config())}
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(msg, tools)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
