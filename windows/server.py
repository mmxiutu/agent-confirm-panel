"""Agent confirm panel server.

WebSocket clients and HTTP share port 8765. websockets 16 rejects HTTP
requests that have a body, so this process accepts TCP itself: Upgrade
requests are handed to websockets, and POST /push plus POST /action are
answered directly.
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from datetime import datetime

from websockets.asyncio.connection import Connection
from websockets.asyncio.server import Server, ServerConnection
from websockets.server import ServerProtocol

HOST = "0.0.0.0"
PORT = 8765
APPROVAL_TIMEOUT_SECONDS = 170
MAX_HTTP_BYTES = 1_048_576
LOOPBACK = {"127.0.0.1", "::1"}
TERMINAL_ACTIONS = {"approve", "reject", "stop_task"}
OBSERVE_ACTIONS = {"view_detail", "clear_alert"}


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", flush=True)


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
        },
    }
    return json.dumps({"type": "state_update", "payload": payload}, ensure_ascii=False)


def follow_up(request_id: str, body: dict, action: str) -> str:
    summary = body.get("summary") or ""
    if action == "approve":
        status = "running"
        summary = f"已同意 {summary}".strip()
    elif action == "stop_task":
        status = "rejected"
        summary = f"已终止 {summary}".strip()
    elif action == "reject":
        status = "rejected"
        summary = f"已拒绝 {summary}".strip()
    else:
        status = "timeout"
        summary = f"审批超时 {summary}".strip()
    updated = dict(body)
    updated["summary"] = summary
    return state_message(request_id, updated, status)


class Panel:
    def __init__(self) -> None:
        self.clients: set[Connection] = set()
        self.pending: dict | None = None
        self.last_message: str | None = None

    async def broadcast(self, message: str) -> None:
        self.last_message = message
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
        await self.decide(request_id, action, body=None)

    async def handle_push(self, body: dict) -> tuple[int, dict]:
        status = str(body.get("status") or "waiting_confirm")
        if status != "waiting_confirm":
            request_id = str(uuid.uuid4())
            message = state_message(request_id, body, status)
            log(f"broadcast request_id={request_id} status={status}")
            await self.broadcast(message)
            return 200, {"request_id": request_id, "action": "broadcast"}

        if self.pending is not None:
            log(f"push rejected busy current={self.pending['id']}")
            return 409, {"error": "approval already pending", "action": "reject"}

        request_id = str(uuid.uuid4())
        message = state_message(request_id, body, "waiting_confirm")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending = {"id": request_id, "future": future, "body": body}
        log(f"push request_id={request_id} status=waiting_confirm summary={body.get('summary') or ''}")
        await self.broadcast(message)
        try:
            action = await asyncio.wait_for(future, APPROVAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if self.pending and self.pending["id"] == request_id:
                self.pending = None
                log(f"decision request_id={request_id} action=timeout")
                await self.broadcast(follow_up(request_id, body, "timeout"))
            return 200, {"request_id": request_id, "action": "timeout"}
        log(f"decision request_id={request_id} action={action}")
        return 200, {"request_id": request_id, "action": action}

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
        matched = await self.decide(request_id, action, body)
        if action in OBSERVE_ACTIONS:
            return 200, {"request_id": request_id, "action": action, "matched": False}
        if not matched:
            return 409, {"error": "no matching approval", "request_id": request_id, "action": action}
        return 200, {"request_id": request_id, "action": action}

    async def decide(self, request_id: str, action: str, body: dict | None) -> bool:
        if action in OBSERVE_ACTIONS:
            return False
        pending = self.pending
        if pending is None or pending["id"] != request_id or action not in TERMINAL_ACTIONS:
            log(f"ignored action request_id={request_id} action={action}")
            return False
        future: asyncio.Future[str] = pending["future"]
        if future.done():
            return False
        self.pending = None
        future.set_result(action)
        source = pending["body"] if body is None else pending["body"]
        await self.broadcast(follow_up(request_id, source, action))
        return True


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
        if self.method != "POST" or self.path not in {"/push", "/action"}:
            self._send(404, "Not Found", {"error": "not found"})
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
                status, result = await self.panel.handle_push(parsed)
                reason = "Conflict" if status == 409 else "OK"
            else:
                status, result = await self.panel.handle_action(parsed, peer)
                reason = {400: "Bad Request", 403: "Forbidden", 409: "Conflict"}.get(status, "OK")
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


async def serve() -> None:
    panel = Panel()
    ws_server = Server(panel.handle_socket, open_timeout=10, server_header="agent-confirm-panel")
    loop = asyncio.get_running_loop()
    tcp = await loop.create_server(lambda: DemuxProtocol(panel, ws_server), HOST, PORT)
    ws_server.wrap(tcp)
    addresses = ", ".join(lan_ips())
    log(f"listening on {HOST}:{PORT}")
    log(f"lan addresses {addresses}")
    log("websocket path ws://<lan-ip>:8765")
    log("local approval POST http://127.0.0.1:8765/action")
    async with tcp:
        await tcp.serve_forever()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
