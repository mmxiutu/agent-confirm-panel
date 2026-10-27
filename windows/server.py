"""Agent confirm panel server.

WebSocket clients and HTTP share port 8765. websockets 16 rejects HTTP
requests that have a body, so this process accepts TCP itself: Upgrade
requests are handed to websockets, and POST routes are answered directly.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
import uuid
from datetime import datetime

from websockets.asyncio.connection import Connection
from websockets.asyncio.server import Server, ServerConnection
from websockets.server import ServerProtocol

from panel_proto import (
    UNKNOWN_TITLE,
    coerce_answers,
    normalize_answers,
    public_source,
    sanitize_questions,
    short_title,
    title_from_transcript,
)
from voice_paste import paste_into_cursor

HOST = "0.0.0.0"
PORT = 8765
APPROVAL_TIMEOUT_SECONDS = 170
SOURCE_TTL_SECONDS = 180
MAX_HTTP_BYTES = 1_048_576
MAX_TRACKED = 200
LOOPBACK = {"127.0.0.1", "::1"}
CONFIRM_ACTIONS = {"approve", "reject", "stop_task"}
CHOICE_ACTIONS = {"choose", "reject", "stop_task"}
OBSERVE_ACTIONS = {"view_detail", "clear_alert"}
WAITING_STATUSES = {"waiting_confirm", "waiting_choice"}


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {message}\n"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(line.encode(encoding, errors="replace"))
        buffer.flush()
        return
    print(line, end="", flush=True)


def lan_ips() -> list[str]:
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    if found:
        return found
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            return [probe.getsockname()[0]]
        finally:
            probe.close()
    except OSError:
        return ["127.0.0.1"]


def state_message(request_id: str, body: dict, status: str | None = None) -> str:
    extra = body.get("extra") if isinstance(body.get("extra"), dict) else {}
    source = extra.get("source") if isinstance(extra.get("source"), dict) else {}
    payload = {
        "request_id": request_id,
        "status": status or body.get("status") or "waiting_confirm",
        "summary": body.get("summary") or "",
        "detail": body.get("detail") or "",
        "extra": {
            "command": extra.get("command") or "",
            "file_path": extra.get("file_path") or "",
            "risk_level": extra.get("risk_level") or "normal",
            "tool": extra.get("tool") or "",
            "source": public_source(source),
            "questions": sanitize_questions(extra.get("questions")),
        },
    }
    return json.dumps({"type": "state_update", "payload": payload}, ensure_ascii=False)


def follow_up(request_id: str, body: dict, action: str) -> str:
    summary = str(body.get("summary") or "")
    extra = body.get("extra") if isinstance(body.get("extra"), dict) else {}
    choice = extra.get("tool") == "choice" or body.get("status") == "waiting_choice"
    if action == "approve":
        status = "running"
        summary = f"已同意 {summary}".strip()
    elif action == "choose":
        status = "done"
        summary = f"已选择 {summary}".strip()
    elif action == "stop_task":
        status = "rejected"
        summary = f"已终止 {summary}".strip()
    elif action == "reject":
        status = "rejected"
        prefix = "已拒绝回答" if choice else "已拒绝"
        summary = f"{prefix} {summary}".strip()
    else:
        status = "timeout"
        prefix = "选择超时" if choice else "审批超时"
        summary = f"{prefix} {summary}".strip()
    updated = dict(body)
    updated["summary"] = summary
    return state_message(request_id, updated, status)


class Panel:
    def __init__(self) -> None:
        self.clients: set[Connection] = set()
        self.pending: dict | None = None
        self.last_message: str | None = None
        self.phase = "idle"
        self.running: dict | None = None
        self.titles: dict[str, str] = {}
        self.sessions: dict[str, dict] = {}
        self.subagents: dict[str, dict] = {}
        self.source_stash: dict[str, list[dict]] = {}

    async def broadcast(self, message: str) -> None:
        self.last_message = message
        await self.notify(message)

    async def notify(self, message: str) -> None:
        dead: list[Connection] = []
        for client in list(self.clients):
            try:
                await client.send(message)
            except Exception:
                dead.append(client)
        for client in dead:
            self.clients.discard(client)

    async def handle_socket(self, websocket: ServerConnection) -> None:
        self.clients.add(websocket)
        peer = websocket.remote_address
        log(f"client connected peer={peer} online={len(self.clients)}")
        if self.last_message:
            try:
                await websocket.send(self.last_message)
            except Exception:
                self.clients.discard(websocket)
                return
        try:
            async for raw in websocket:
                await self.handle_client_message(raw, peer)
        finally:
            self.clients.discard(websocket)
            log(f"client disconnected peer={peer} online={len(self.clients)}")

    async def handle_client_message(self, raw: str | bytes, peer: object) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log(f"invalid client json peer={peer}")
            return
        if not isinstance(data, dict):
            log(f"invalid client payload peer={peer}")
            return
        action = str(data.get("action") or "")
        request_id = str(data.get("request_id") or "")
        remark = str(data.get("remark") or "")
        log(
            f"user_action source=websocket peer={peer} "
            f"request_id={request_id} action={action} remark={remark}"
        )
        if action == "voice_input":
            text = remark.strip()
            if text == "":
                log("voice_input empty")
                await self.notify('{"type":"voice_result","ok":false,"message":"没有识别到语音"}')
                return
            reason = await asyncio.to_thread(paste_into_cursor, text)
            ok = reason == ""
            message = "已填入电脑" if ok else reason
            log(f"voice_input ok={ok} chars={len(text)} reason={reason}")
            await self.notify(
                json.dumps({"type": "voice_result", "ok": ok, "message": message}, ensure_ascii=False)
            )
            return
        await self.decide(request_id, action, data)

    def register_session(self, body: dict) -> None:
        conversation_id = str(body.get("conversation_id") or "")[:80]
        if conversation_id == "":
            return
        kind = "background" if body.get("kind") == "background" else "chat"
        transcript_path = str(body.get("transcript_path") or "")
        self.sessions[conversation_id] = {
            "kind": kind,
            "project": str(body.get("project") or "")[:80],
            "transcript_path": transcript_path,
        }
        self._trim(self.sessions)
        self.lookup_title(conversation_id, transcript_path)
        log(f"session conversation_id={conversation_id} kind={kind}")

    def register_subagent(self, body: dict) -> None:
        subagent_id = str(body.get("subagent_id") or "")[:80]
        if subagent_id == "":
            return
        self.subagents[subagent_id] = {
            "task": str(body.get("task") or ""),
            "parent": str(body.get("parent_conversation_id") or "")[:80],
            "project": str(body.get("project") or "")[:80],
        }
        self._trim(self.subagents)
        log(f"subagent id={subagent_id} parent={self.subagents[subagent_id]['parent']}")

    def remember_source(self, key: str, raw: dict) -> None:
        now = time.monotonic()
        self._prune_sources(now)
        resolved = self.resolve_source(raw if isinstance(raw, dict) else {})
        self.source_stash.setdefault(key, []).append({"at": now, "source": resolved})
        log(
            f"source stashed key={key[:8]} conversation_id={resolved['conversation_id']} "
            f"title={resolved['title']}"
        )

    def lookup_title(self, conversation_id: str, transcript_path: str) -> str:
        if conversation_id == "":
            return ""
        cached = self.titles.get(conversation_id, "")
        if cached:
            return cached
        fresh = title_from_transcript(transcript_path)
        if fresh:
            self.titles[conversation_id] = fresh
            self._trim(self.titles)
        return fresh

    def resolve_source(self, raw: dict) -> dict:
        conversation_id = str(raw.get("conversation_id") or "")[:80]
        parent_id = str(raw.get("parent_conversation_id") or "")[:80]
        project = str(raw.get("project") or "")[:80]
        kind = str(raw.get("kind") or "chat")
        if kind not in {"chat", "subagent", "background"}:
            kind = "chat"
        task = str(raw.get("task") or "")
        transcript_path = str(raw.get("transcript_path") or "")

        subagent = self.subagents.get(conversation_id)
        if subagent is not None:
            kind = "subagent"
            task = task or str(subagent.get("task") or "")
            parent_id = parent_id or str(subagent.get("parent") or "")
            project = project or str(subagent.get("project") or "")

        session = self.sessions.get(conversation_id)
        if session is not None:
            project = project or str(session.get("project") or "")
            if kind == "chat" and session.get("kind") == "background":
                kind = "background"
            if transcript_path == "":
                transcript_path = str(session.get("transcript_path") or "")

        if kind == "subagent" and task.strip():
            title = short_title(task)
        else:
            title = self.lookup_title(conversation_id, transcript_path)
            if title == "" and parent_id:
                parent = self.sessions.get(parent_id)
                parent_path = str(parent.get("transcript_path") or "") if parent else ""
                title = self.lookup_title(parent_id, parent_path)
                project = project or (str(parent.get("project") or "") if parent else "")
            if title == "" and conversation_id:
                title = conversation_id[:8]
        if project == "" and parent_id:
            parent = self.sessions.get(parent_id)
            if parent is not None:
                project = str(parent.get("project") or "")
        return public_source(
            {
                "conversation_id": conversation_id,
                "title": title,
                "project": project,
                "kind": kind,
            }
        )

    def take_source(self, key: str) -> dict | None:
        now = time.monotonic()
        self._prune_sources(now)
        bucket = self.source_stash.get(key)
        if not bucket:
            return None
        found = bucket.pop(0)["source"]
        if not bucket:
            del self.source_stash[key]
        return found

    def lone_project(self) -> str:
        projects: list[str] = []
        for bucket in self.source_stash.values():
            for item in bucket:
                project = str(item["source"].get("project") or "")
                if project and project not in projects:
                    projects.append(project)
        if len(projects) == 1:
            return projects[0]
        return ""

    def prepare_body(self, body: dict, peer_ip: str) -> dict:
        prepared = dict(body)
        extra = dict(prepared.get("extra") or {}) if isinstance(prepared.get("extra"), dict) else {}
        raw_source = dict(extra.get("source") or {}) if isinstance(extra.get("source"), dict) else {}
        if peer_ip not in LOOPBACK:
            raw_source.pop("transcript_path", None)
        status = str(prepared.get("status") or "waiting_confirm")
        key = str(prepared.get("source_key") or "")
        if status == "waiting_choice":
            stashed = self.take_source(key) if key else None
            if stashed is not None:
                extra["source"] = stashed
            elif raw_source.get("conversation_id") or raw_source.get("title") or raw_source.get("project"):
                extra["source"] = self.resolve_source(raw_source)
            else:
                extra["source"] = public_source(
                    {
                        "conversation_id": "",
                        "title": UNKNOWN_TITLE,
                        "project": self.lone_project(),
                        "kind": "chat",
                    }
                )
        else:
            extra["source"] = self.resolve_source(raw_source)
        extra["questions"] = sanitize_questions(extra.get("questions"))
        prepared["extra"] = extra
        return prepared

    async def handle_push(self, body: dict, peer_ip: str) -> tuple[int, dict]:
        status = str(body.get("status") or "waiting_confirm")
        if status in WAITING_STATUSES and self.pending is not None:
            log(f"push rejected busy current={self.pending['id']}")
            return 409, {"error": "approval already pending", "action": "reject", "answers": []}

        prepared = self.prepare_body(body, peer_ip)
        status = str(prepared.get("status") or "waiting_confirm")
        if status not in WAITING_STATUSES:
            if status in {"done", "error"}:
                return await self.handle_completion(status)
            request_id = str(uuid.uuid4())
            message = state_message(request_id, prepared, status)
            log(f"broadcast request_id={request_id} status={status}")
            await self.broadcast(message)
            return 200, {"request_id": request_id, "action": "broadcast", "answers": []}

        request_id = str(uuid.uuid4())
        message = state_message(request_id, prepared, status)
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self.pending = {
            "id": request_id,
            "future": future,
            "body": prepared,
            "kind": "choice" if status == "waiting_choice" else "confirm",
        }
        self.phase = "waiting"
        self.running = None
        try:
            log(f"push request_id={request_id} status={status} summary={prepared.get('summary') or ''}")
            await self.broadcast(message)
        except Exception:
            if self.pending and self.pending["id"] == request_id:
                self.pending = None
                self.phase = "idle"
                self.running = None
            raise
        try:
            result = await asyncio.wait_for(future, APPROVAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if self.pending and self.pending["id"] == request_id:
                self.pending = None
                self.phase = "idle"
                self.running = None
                log(f"decision request_id={request_id} action=timeout")
                await self.broadcast(follow_up(request_id, prepared, "timeout"))
            return 200, {"request_id": request_id, "action": "timeout", "answers": []}
        action = str(result.get("action") or "reject")
        answers = result.get("answers") if isinstance(result.get("answers"), list) else []
        log(f"decision request_id={request_id} action={action}")
        return 200, {"request_id": request_id, "action": action, "answers": answers}

    def status(self) -> dict:
        return {
            "ok": True,
            "clients": len(self.clients),
            "pending": self.pending is not None,
        }

    async def handle_action(self, body: dict, peer_ip: str) -> tuple[int, dict]:
        if peer_ip not in LOOPBACK:
            log(f"action rejected remote peer={peer_ip}")
            return 403, {"error": "action is local only"}
        request_id = str(body.get("request_id") or "")
        action = str(body.get("action") or "")
        remark = str(body.get("remark") or "")
        log(f"user_action source=http peer={peer_ip} request_id={request_id} action={action} remark={remark}")
        if not request_id:
            return 400, {"error": "request_id is required"}
        outcome = await self.decide(request_id, action, body)
        if action in OBSERVE_ACTIONS:
            return 200, {"request_id": request_id, "action": action, "matched": False, "answers": []}
        if outcome == "invalid":
            return 400, {"error": "answers are incomplete", "request_id": request_id, "action": action}
        if outcome != "ok":
            return 409, {"error": "no matching approval", "request_id": request_id, "action": action}
        return 200, {"request_id": request_id, "action": action, "answers": []}

    async def decide(self, request_id: str, action: str, client: dict | None) -> str:
        if action in OBSERVE_ACTIONS:
            return "ignore"
        pending = self.pending
        if pending is None or pending["id"] != request_id:
            log(f"ignored action request_id={request_id} action={action}")
            return "ignore"
        kind = str(pending.get("kind") or "confirm")
        allowed = CHOICE_ACTIONS if kind == "choice" else CONFIRM_ACTIONS
        if action not in allowed:
            log(f"ignored action request_id={request_id} action={action} kind={kind}")
            return "ignore"
        answers: list[dict] = []
        if action == "choose":
            questions = []
            body = pending["body"]
            extra = body.get("extra") if isinstance(body.get("extra"), dict) else {}
            raw_questions = extra.get("questions")
            if isinstance(raw_questions, list):
                questions = raw_questions
            parsed = normalize_answers(questions, coerce_answers(None if client is None else client.get("answers")))
            if parsed is None:
                log(f"invalid choice request_id={request_id}")
                return "invalid"
            answers = parsed
        future: asyncio.Future[dict] = pending["future"]
        if future.done():
            return "ignore"
        self.pending = None
        future.set_result({"action": action, "answers": answers})
        source = pending["body"]
        if action == "approve":
            self.phase = "running"
            self.running = {"id": request_id, "body": source}
        else:
            self.phase = "idle"
            self.running = None
        await self.broadcast(follow_up(request_id, source, action))
        return "ok"

    async def handle_completion(self, status: str) -> tuple[int, dict]:
        running = self.running
        if self.pending is not None or self.phase != "running" or running is None:
            log(f"completion ignored status={status} phase={self.phase}")
            return 200, {"action": "ignored", "status": status, "answers": []}
        request_id = str(running["id"])
        source = dict(running["body"])
        summary = str(source.get("summary") or "")
        if status == "done":
            source["summary"] = f"执行完毕 {summary}".strip()
        else:
            source["summary"] = f"执行失败 {summary}".strip()
        self.phase = "idle"
        self.running = None
        log(f"completion request_id={request_id} status={status}")
        await self.broadcast(state_message(request_id, source, status))
        return 200, {"request_id": request_id, "action": "broadcast", "status": status, "answers": []}

    def _trim(self, store: dict) -> None:
        while len(store) > MAX_TRACKED:
            store.pop(next(iter(store)))

    def _prune_sources(self, now: float) -> None:
        empty: list[str] = []
        for key, bucket in self.source_stash.items():
            kept = [item for item in bucket if now - float(item["at"]) <= SOURCE_TTL_SECONDS]
            self.source_stash[key] = kept
            if not kept:
                empty.append(key)
        for key in empty:
            del self.source_stash[key]


def json_response(status: int, reason: str, payload: dict) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(raw)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return head.encode("ascii") + raw


class DemuxProtocol(asyncio.Protocol):
    def __init__(self, panel: Panel, ws_server: Server) -> None:
        self.panel = panel
        self.ws_server = ws_server
        self.transport: asyncio.Transport | None = None
        self.buf = bytearray()
        self.switched = False
        self.header_done = False
        self.method = ""
        self.path = ""
        self.headers: dict[str, str] = {}
        self.body_len = 0
        self.header_size = 0
        self.continued = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def data_received(self, data: bytes) -> None:
        if self.switched or self.transport is None:
            return
        self.buf.extend(data)
        if len(self.buf) > MAX_HTTP_BYTES:
            self._send(400, "Bad Request", {"error": "request too large"})
            return
        if not self.header_done:
            marker = self.buf.find(b"\r\n\r\n")
            if marker < 0:
                return
            self.header_done = True
            self.header_size = marker + 4
            if not self._parse_headers(bytes(self.buf[:marker])):
                self._send(400, "Bad Request", {"error": "malformed request"})
                return
            if "websocket" in self.headers.get("upgrade", "").lower():
                self._switch_websocket()
                return
            if "100-continue" in self.headers.get("expect", "").lower():
                self.continued = True
                self.transport.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        body = bytes(self.buf[self.header_size :])
        if len(body) < self.body_len:
            return
        self.transport.pause_reading()
        payload = body[: self.body_len]
        asyncio.get_running_loop().create_task(self._handle_http(payload))

    def _parse_headers(self, head: bytes) -> bool:
        text = head.decode("iso-8859-1", errors="replace")
        lines = text.split("\r\n")
        if not lines or not lines[0]:
            return False
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return False
        self.method = parts[0].upper()
        self.path = parts[1].split("?", 1)[0]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        self.headers = headers
        raw_length = headers.get("content-length", "0")
        try:
            self.body_len = int(raw_length)
        except ValueError:
            return False
        return 0 <= self.body_len <= MAX_HTTP_BYTES

    def _switch_websocket(self) -> None:
        assert self.transport is not None
        self.switched = True
        protocol = ServerProtocol(max_size=MAX_HTTP_BYTES)
        connection = ServerConnection(
            protocol,
            self.ws_server,
            ping_interval=None,
            ping_timeout=None,
        )
        raw = bytes(self.buf)
        self.transport.set_protocol(connection)
        connection.connection_made(self.transport)
        connection.data_received(raw)

    async def _handle_http(self, payload: bytes) -> None:
        peer = ""
        if self.transport is not None:
            address = self.transport.get_extra_info("peername")
            if address:
                peer = address[0]
        if self.method == "GET" and self.path == "/status":
            if peer not in LOOPBACK:
                self._send(403, "Forbidden", {"error": "status is local only"})
                return
            self._send(200, "OK", self.panel.status())
            return
        local_posts = {"/push", "/action", "/session", "/subagent", "/source"}
        if self.method != "POST" or self.path not in local_posts:
            self._send(404, "Not Found", {"error": "not found"})
            return
        if self.path != "/push" and peer not in LOOPBACK:
            self._send(403, "Forbidden", {"error": "local only"})
            return
        try:
            parsed = json.loads(payload.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, "Bad Request", {"error": "invalid json"})
            return
        if not isinstance(parsed, dict):
            self._send(400, "Bad Request", {"error": "json object required"})
            return
        try:
            if self.path == "/push":
                status, result = await self.panel.handle_push(parsed, peer)
                reason = "Conflict" if status == 409 else "OK"
            elif self.path == "/action":
                status, result = await self.panel.handle_action(parsed, peer)
                reason = {400: "Bad Request", 403: "Forbidden", 409: "Conflict"}.get(status, "OK")
            elif self.path == "/session":
                self.panel.register_session(parsed)
                status, reason, result = 200, "OK", {"ok": True}
            elif self.path == "/subagent":
                self.panel.register_subagent(parsed)
                status, reason, result = 200, "OK", {"ok": True}
            else:
                key = str(parsed.get("key") or "")
                source = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
                if key == "":
                    self._send(400, "Bad Request", {"error": "key is required"})
                    return
                self.panel.remember_source(key, source)
                status, reason, result = 200, "OK", {"ok": True}
        except Exception as exc:
            log(f"http error path={self.path} error={exc}")
            self._send(500, "Internal Server Error", {"error": "internal error"})
            return
        self._send(status, reason, result)

    def _send(self, status: int, reason: str, payload: dict) -> None:
        if self.transport is None:
            return
        try:
            self.transport.write(json_response(status, reason, payload))
        except Exception as exc:
            log(f"http write failed error={exc}")
        try:
            self.transport.close()
        except Exception:
            pass


async def serve(port: int = PORT) -> None:
    panel = Panel()
    ws_server = Server(panel.handle_socket, open_timeout=10, server_header="agent-confirm-panel")
    loop = asyncio.get_running_loop()
    tcp = await loop.create_server(lambda: DemuxProtocol(panel, ws_server), HOST, port)
    ws_server.wrap(tcp)
    addresses = ", ".join(lan_ips())
    log(f"listening on {HOST}:{port}")
    log(f"lan addresses {addresses}")
    log(f"websocket path ws://<lan-ip>:{port}")
    log(f"local approval POST http://127.0.0.1:{port}/action")
    async with tcp:
        await tcp.serve_forever()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
