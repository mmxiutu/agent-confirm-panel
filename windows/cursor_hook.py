"""Cursor command hook.

Reads a beforeShellExecution or beforeMCPExecution payload from stdin.
When the panel server is running and a phone is connected, the hook waits
for the phone and returns allow or deny. Otherwise it returns ask, and
Cursor keeps its own confirmation behavior.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from panel_proto import project_name, question_key, sanitize_questions

PUSH_URL = "http://127.0.0.1:8765/push"
STATUS_URL = "http://127.0.0.1:8765/status"
SESSION_URL = "http://127.0.0.1:8765/session"
SUBAGENT_URL = "http://127.0.0.1:8765/subagent"
SOURCE_URL = "http://127.0.0.1:8765/source"
HTTP_TIMEOUT_SECONDS = 175
STATUS_TIMEOUT_SECONDS = 2
NOTE_TIMEOUT_SECONDS = 0.4
PANEL_MCP_SERVER = "agent-confirm-panel"
PANEL_ASK_TOOL = "ask"
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


def parse_tool_input(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def raw_source(hook_input: dict) -> dict:
    conversation_id = str(hook_input.get("conversation_id") or hook_input.get("session_id") or "")
    subagent_id = str(hook_input.get("subagent_id") or "")
    kind = "chat"
    if subagent_id:
        kind = "subagent"
        conversation_id = subagent_id
    elif hook_input.get("is_background_agent") is True:
        kind = "background"
    return {
        "conversation_id": conversation_id,
        "parent_conversation_id": str(hook_input.get("parent_conversation_id") or ""),
        "project": project_name(hook_input.get("workspace_roots"), str(hook_input.get("cwd") or "")),
        "kind": kind,
        "transcript_path": str(hook_input.get("transcript_path") or ""),
        "task": str(hook_input.get("task") or ""),
    }


def is_panel_ask(hook_input: dict) -> bool:
    return (
        str(hook_input.get("mcp_server_name") or "") == PANEL_MCP_SERVER
        and str(hook_input.get("tool_name") or "") == PANEL_ASK_TOOL
    )


def post_json(url: str, body: dict, timeout: float) -> dict:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def note(url: str, body: dict) -> None:
    try:
        post_json(url, body, NOTE_TIMEOUT_SECONDS)
    except Exception as exc:
        sys.stderr.write(f"panel note skipped: {exc}\n")


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
                "source": raw_source(hook_input),
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
            "source": raw_source(hook_input),
        },
    }


def panel_is_ready() -> bool:
    request = urllib.request.Request(STATUS_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=STATUS_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return False
    if not isinstance(payload, dict) or not payload.get("ok"):
        return False
    try:
        clients = int(payload.get("clients") or 0)
    except (TypeError, ValueError):
        return False
    return clients > 0


def push(body: dict, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict:
    return post_json(PUSH_URL, body, timeout)


def register_session(hook_input: dict) -> None:
    note(
        SESSION_URL,
        {
            "conversation_id": str(hook_input.get("session_id") or hook_input.get("conversation_id") or ""),
            "project": project_name(hook_input.get("workspace_roots"), str(hook_input.get("cwd") or "")),
            "kind": "background" if hook_input.get("is_background_agent") is True else "chat",
            "transcript_path": str(hook_input.get("transcript_path") or ""),
        },
    )
    sys.stdout.write("{}\n")
    sys.stdout.flush()


def register_subagent(hook_input: dict) -> None:
    note(
        SUBAGENT_URL,
        {
            "subagent_id": str(hook_input.get("subagent_id") or ""),
            "parent_conversation_id": str(
                hook_input.get("parent_conversation_id") or hook_input.get("conversation_id") or ""
            ),
            "project": project_name(hook_input.get("workspace_roots"), str(hook_input.get("cwd") or "")),
            "task": str(hook_input.get("task") or ""),
        },
    )
    emit("allow")


def allow_panel_ask(hook_input: dict) -> None:
    questions = sanitize_questions(parse_tool_input(hook_input.get("tool_input")).get("questions"))
    if questions:
        note(SOURCE_URL, {"key": question_key(questions), "source": raw_source(hook_input)})
    emit("allow")


def report_finished(hook_input: dict) -> None:
    if is_panel_ask(hook_input):
        return
    event = str(hook_input.get("hook_event_name") or "")
    if event == "afterMCPExecution":
        tool_name = str(hook_input.get("tool_name") or "")
        server_name = str(hook_input.get("mcp_server_name") or "")
        summary = f"MCP {server_name} / {tool_name}".strip()
        detail = str(hook_input.get("result_json") or "")[:800]
        command = tool_name
        tool = "mcp"
    else:
        command = str(hook_input.get("command") or "")
        summary = command[:180] or "命令"
        detail = str(hook_input.get("output") or "")[:800]
        tool = "shell"
    if not panel_is_ready():
        return
    body = {
        "status": "done",
        "summary": summary,
        "detail": detail,
        "extra": {
            "command": command,
            "file_path": "",
            "risk_level": "normal",
            "tool": tool,
            "source": raw_source(hook_input),
        },
    }
    try:
        push(body, timeout=5)
    except Exception as exc:
        sys.stderr.write(f"panel completion skipped: {exc}\n")


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
    event = str(hook_input.get("hook_event_name") or "")
    if event == "sessionStart":
        register_session(hook_input)
        return
    if event == "subagentStart":
        register_subagent(hook_input)
        return
    if event in ("afterShellExecution", "afterMCPExecution"):
        report_finished(hook_input)
        return
    if is_panel_ask(hook_input):
        allow_panel_ask(hook_input)
        return
    if is_panel_server_launch(hook_input):
        emit("allow")
        return
    if not panel_is_ready():
        emit("ask", "手机面板未连接，改由 Cursor 确认。")
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
