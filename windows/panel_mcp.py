"""stdio MCP server.

Exposes one tool, ask. Cursor launches this process. The tool blocks until
the phone panel answers, and returns that answer to the agent.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from panel_proto import MAX_OPTIONS, MAX_QUESTIONS, present_answers, question_key, sanitize_questions

PUSH_URL = "http://127.0.0.1:8765/push"
STATUS_URL = "http://127.0.0.1:8765/status"
PUSH_TIMEOUT_SECONDS = 175
STATUS_TIMEOUT_SECONDS = 2
SERVER_NAME = "agent-confirm-panel"

ASK_TOOL = {
    "name": "ask",
    "description": (
        "Ask the user on the phone confirm panel instead of AskQuestion. "
        "Use this when the user must choose among options or add a custom answer. "
        f"Send 1 to {MAX_QUESTIONS} questions. Each question needs id, prompt, and "
        f"1 to {MAX_OPTIONS} options with id and label. Set allow_multiple to true "
        "when several options may be chosen. The phone always shows a custom text "
        "field for every question. This call waits until the phone answers. "
        "If reason is panel_offline, use AskQuestion on the desktop. "
        "If reason is panel_busy, retry once, then use AskQuestion. "
        "choose means use answers. reject means the user declined. "
        "stop_task means stop the current task. timeout means do not continue a risky step."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title shown on the phone.",
            },
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_QUESTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "allow_multiple": {"type": "boolean"},
                        "options": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_OPTIONS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                },
                                "required": ["id", "label"],
                            },
                        },
                    },
                    "required": ["id", "prompt", "options"],
                },
            },
        },
        "required": ["questions"],
    },
}


def log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def read_message() -> dict | None:
    header = bytearray()
    while not header.endswith(b"\r\n\r\n") and not header.endswith(b"\n\n"):
        chunk = sys.stdin.buffer.read(1)
        if chunk == b"":
            return None
        header.extend(chunk)
        if len(header) > 64_000:
            raise ValueError("MCP header is too large")
    length = 0
    for line in header.decode("ascii", errors="replace").splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length <= 0:
        raise ValueError("MCP frame is missing Content-Length")
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        return None
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("MCP frame is not an object")
    return parsed


def write_message(payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def rpc_result(message_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def rpc_error(message_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def tool_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": is_error,
    }


def get_status() -> dict | None:
    request = urllib.request.Request(STATUS_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=STATUS_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def post_push(body: dict) -> tuple[int, dict]:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        PUSH_URL,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PUSH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(detail)
        except json.JSONDecodeError:
            payload = {"error": detail}
        return exc.code, payload if isinstance(payload, dict) else {}


def validate_questions(raw: object) -> tuple[list[dict] | None, str]:
    if not isinstance(raw, list) or len(raw) == 0:
        return None, "questions 不能为空"
    if len(raw) > MAX_QUESTIONS:
        return None, f"一次最多 {MAX_QUESTIONS} 道题"
    for item in raw:
        if not isinstance(item, dict):
            return None, "每道题都要有 prompt 和 options"
        options = item.get("options")
        if not isinstance(options, list):
            return None, "每道题至少要有一个选项"
        labels = [
            option
            for option in options
            if isinstance(option, dict) and str(option.get("label") or "").strip()
        ]
        if len(labels) == 0:
            return None, "每道题至少要有一个选项"
        if len(options) > MAX_OPTIONS:
            return None, f"每题最多 {MAX_OPTIONS} 个选项"
    cleaned = sanitize_questions(raw)
    if not cleaned:
        return None, "questions 不能为空"
    return cleaned, ""


def ask(arguments: dict) -> dict:
    questions, error = validate_questions(arguments.get("questions"))
    if questions is None:
        return tool_result({"ok": False, "reason": "invalid_questions", "message": error}, True)
    status = get_status()
    clients = 0
    if status is not None:
        try:
            clients = int(status.get("clients") or 0)
        except (TypeError, ValueError):
            clients = 0
    if status is None or not status.get("ok") or clients < 1:
        return tool_result(
            {
                "ok": False,
                "reason": "panel_offline",
                "message": "手机面板未连接。请改用 AskQuestion，在桌面让用户选择。",
            }
        )
    if status.get("pending"):
        return tool_result(
            {
                "ok": False,
                "reason": "panel_busy",
                "message": "手机面板正在处理另一个聊天的请求。请稍后重试一次；若仍然忙碌，改用 AskQuestion。",
            }
        )
    title = str(arguments.get("title") or "").strip()
    summary = title or str(questions[0]["prompt"])
    body = {
        "status": "waiting_choice",
        "summary": summary[:180],
        "detail": "",
        "source_key": question_key(questions),
        "extra": {
            "command": "",
            "file_path": "",
            "risk_level": "normal",
            "tool": "choice",
            "questions": questions,
        },
    }
    try:
        code, result = post_push(body)
    except Exception as exc:
        log(f"ask push failed: {exc}")
        return tool_result(
            {
                "ok": False,
                "reason": "panel_offline",
                "message": "手机面板没有响应。请改用 AskQuestion，在桌面让用户选择。",
            }
        )
    if code == 409:
        return tool_result(
            {
                "ok": False,
                "reason": "panel_busy",
                "message": "手机面板正在处理另一个聊天的请求。请稍后重试一次；若仍然忙碌，改用 AskQuestion。",
            }
        )
    action = str(result.get("action") or "timeout")
    answers = result.get("answers") if isinstance(result.get("answers"), list) else []
    if action == "choose":
        return tool_result({"ok": True, "action": "choose", "answers": present_answers(questions, answers)})
    if action == "reject":
        return tool_result(
            {
                "ok": True,
                "action": "reject",
                "message": "用户拒绝回答。不要重复同一问题。",
            }
        )
    if action == "stop_task":
        return tool_result(
            {
                "ok": True,
                "action": "stop_task",
                "message": "用户要求终止当前任务，不要继续执行。",
            }
        )
    return tool_result(
        {
            "ok": True,
            "action": "timeout",
            "message": "用户没有在时限内回答。不要继续执行有风险的操作。",
        }
    )


def handle(message: dict) -> dict | None:
    method = str(message.get("method") or "")
    if method.startswith("notifications/"):
        return None
    message_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        version = str(params.get("protocolVersion") or "2024-11-05")
        return rpc_result(
            message_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            },
        )
    if method == "ping":
        return rpc_result(message_id, {})
    if method == "tools/list":
        return rpc_result(message_id, {"tools": [ASK_TOOL]})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name != "ask":
            return rpc_result(message_id, tool_result({"ok": False, "message": "未知工具"}, True))
        return rpc_result(message_id, ask(arguments))
    if message_id is None:
        return None
    return rpc_error(message_id, -32601, f"unknown method {method}")


def main() -> None:
    while True:
        try:
            message = read_message()
        except Exception as exc:
            log(f"mcp read failed: {exc}")
            return
        if message is None:
            return
        try:
            response = handle(message)
        except Exception as exc:
            log(f"mcp handle failed: {exc}")
            message_id = message.get("id")
            if message_id is None:
                continue
            response = rpc_error(message_id, -32603, "internal error")
        if response is not None:
            write_message(response)


if __name__ == "__main__":
    main()
