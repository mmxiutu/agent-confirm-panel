"""Cursor command hook.

Reads a beforeShellExecution or beforeMCPExecution payload from stdin,
blocks on the local panel server, and writes an allow or deny decision
to stdout. Anything other than an explicit approve becomes deny.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

PUSH_URL = "http://127.0.0.1:8765/push"
HTTP_TIMEOUT_SECONDS = 175
HIGH_RISK_MARKERS = (
    "rm ",
    "rm\t",
    "del ",
    "erase ",
    "rmdir",
    "rd /s",
    "format ",
    "mkfs",
    "shutdown",
    "remove-item",
    "dd ",
)


def emit(permission: str, reason: str = "") -> None:
    payload = {"permission": permission}
    if reason:
        payload["user_message"] = reason
        payload["agent_message"] = reason
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def risk_level(command: str) -> str:
    folded = command.lower()
    if any(marker in folded for marker in HIGH_RISK_MARKERS):
        return "high"
    return "normal"


def build_push(hook_input: dict) -> dict:
    tool_name = hook_input.get("tool_name")
    if tool_name:
        server_name = str(hook_input.get("mcp_server_name") or "")
        tool_input = hook_input.get("tool_input")
        if not isinstance(tool_input, str):
            tool_input = json.dumps(tool_input, ensure_ascii=False)
        summary = f"MCP {server_name} / {tool_name}".strip()
        return {
            "status": "waiting_confirm",
            "summary": summary,
            "detail": tool_input,
            "extra": {
                "command": str(tool_name),
                "file_path": "",
                "risk_level": "normal",
                "tool": "mcp",
                "mcp_server_name": server_name,
            },
        }

    command = str(hook_input.get("command") or "")
    cwd = str(hook_input.get("cwd") or "")
    return {
        "status": "waiting_confirm",
        "summary": command[:180] or "空命令",
        "detail": command,
        "extra": {
            "command": command,
            "file_path": cwd,
            "risk_level": risk_level(command),
            "tool": "shell",
        },
    }


def push(body: dict) -> dict:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        PUSH_URL,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def read_hook_input() -> dict:
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        raise json.JSONDecodeError("empty stdin", "", 0)
    text = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise json.JSONDecodeError("undecodable stdin", "", 0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise json.JSONDecodeError("stdin is not an object", text, 0)
    return value


def is_panel_server_launch(hook_input: dict) -> bool:
    if hook_input.get("tool_name"):
        return False
    command = str(hook_input.get("command") or "")
    if any(token in command for token in ("&&", "||", ";", "|", "\n", "`")):
        return False
    folded = command.replace("/", "\\").strip().rstrip('"').rstrip("'").lower()
    return folded.endswith("windows\\server.py")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        hook_input = read_hook_input()
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid hook stdin: {exc}\n")
        emit("deny", "审批钩子没有收到有效输入，已拒绝。")
        return
    if not isinstance(hook_input, dict):
        emit("deny", "审批钩子输入格式不正确，已拒绝。")
        return
    if is_panel_server_launch(hook_input):
        emit("allow")
        return
    try:
        result = push(build_push(hook_input))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"panel http {exc.code}: {detail}\n")
        emit("deny", "审批服务拒绝了这次请求，已拒绝执行。")
        return
    except Exception as exc:
        sys.stderr.write(f"panel unreachable: {exc}\n")
        emit("deny", "审批服务没有响应，已拒绝执行。")
        return
    if result.get("action") == "approve":
        emit("allow")
        return
    action = str(result.get("action") or "timeout")
    emit("deny", f"面板决定为 {action}，已拒绝执行。")


if __name__ == "__main__":
    main()
