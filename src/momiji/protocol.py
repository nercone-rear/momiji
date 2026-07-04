from __future__ import annotations

import os
import asyncio
import base64
import hashlib
from typing import Optional, TYPE_CHECKING

from .errors import HTTPViolationError, HTTPError, HTTPReportedViolationError
from .models import Role, Request, Response
from .headers import Headers, CommaHeader, AcceptEncoding
from .finalizer import finalize_request, finalize_response, HOP_BY_HOP_HEADERS
from .websocket import WebSocket, GUID as WEBSOCKET_GUID

if TYPE_CHECKING:
    from .server import Handler

MAX_HEADER_SIZE = 64 * 1024
MAX_BODY_SIZE = 100 * 1024 * 1024

REASON_PHRASES = {
    100: "Continue", 101: "Switching Protocols",
    200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 206: "Partial Content",
    301: "Moved Permanently", 302: "Found", 303: "See Other", 304: "Not Modified", 307: "Temporary Redirect", 308: "Permanent Redirect",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 406: "Not Acceptable", 409: "Conflict", 410: "Gone", 411: "Length Required", 412: "Precondition Failed", 413: "Payload Too Large", 414: "URI Too Long", 415: "Unsupported Media Type", 416: "Range Not Satisfiable", 417: "Expectation Failed", 426: "Upgrade Required", 429: "Too Many Requests", 431: "Request Header Fields Too Large",
    500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout", 505: "HTTP Version Not Supported"
}

def find_body_mode(headers: Headers, *, is_response: bool, status_code: Optional[int] = None) -> tuple[str, int]:
    if is_response and status_code is not None:
        if status_code in (204, 304) or (100 <= status_code < 200):
            return "none", 0

    transfer_encoding_values = headers["Transfer-Encoding"]
    content_length_values = headers["Content-Length"]

    if transfer_encoding_values is not None:
        if content_length_values is not None:
            raise HTTPReportedViolationError(400, "Conflicting Transfer-Encoding and Content-Length")

        tokens = CommaHeader(", ".join(transfer_encoding_values))

        if not tokens.raw or tokens.raw[-1].lower() != "chunked":
            raise HTTPReportedViolationError(400, "Transfer-Encoding must end in chunked")

        return "chunked", 0

    if content_length_values is not None:
        if len(set(content_length_values)) > 1:
            raise HTTPReportedViolationError(400, "Conflicting Content-Length values")

        cl_value = content_length_values[0]

        if not cl_value or not all(c in "0123456789" for c in cl_value):
            raise HTTPReportedViolationError(400, "Invalid Content-Length")

        length = int(cl_value)

        if length < 0:
            raise HTTPReportedViolationError(400, "Invalid Content-Length")

        return "length", length

    if is_response:
        return "close", 0

    return "none", 0

class ChunkedDecoder:
    def __init__(self):
        self.state = "size"
        self.remaining = 0
        self.body = bytearray()
        self.trailer_buffer = b""
        self.done = False

    def feed(self, buffer: bytearray) -> bool:
        while not self.done:
            if self.state == "size":
                idx = buffer.find(b"\r\n")

                if idx == -1:
                    if len(buffer) > 4096:
                        raise HTTPReportedViolationError(400, "chunk size line too long")
                    return False

                line = bytes(buffer[:idx])
                del buffer[:idx + 2]

                size_str = line.split(b";", 1)[0].strip()

                if not size_str or not all(c in b"0123456789abcdefABCDEF" for c in size_str):
                    raise HTTPReportedViolationError(400, "invalid chunk size")

                self.remaining = int(size_str, 16)
                self.state = "trailer" if self.remaining == 0 else "data"

            elif self.state == "data":
                if len(buffer) < self.remaining + 2:
                    return False

                self.body.extend(buffer[:self.remaining])

                if bytes(buffer[self.remaining:self.remaining + 2]) != b"\r\n":
                    raise HTTPReportedViolationError(400, "malformed chunk terminator")

                del buffer[:self.remaining + 2]
                self.state = "size"

            elif self.state == "trailer":
                if len(buffer) < 2:
                    return False

                if bytes(buffer[:2]) == b"\r\n":
                    del buffer[:2]
                    self.done = True
                    continue

                idx = buffer.find(b"\r\n\r\n")

                if idx == -1:
                    if len(buffer) > MAX_HEADER_SIZE:
                        raise HTTPReportedViolationError(400, "trailer section too large")
                    return False

                self.trailer_buffer = bytes(buffer[:idx])
                del buffer[:idx + 4]
                self.done = True

        return True

class MessageParser:
    def __init__(self, *, is_response: bool):
        self.is_response = is_response
        self.buffer = bytearray()
        self.closed_eof = False
        self.reset()

    def reset(self):
        self.stage = "head"
        self.first_line: Optional[str] = None
        self.headers: Optional[Headers] = None
        self.body_mode: Optional[str] = None
        self.content_length = 0
        self.body = bytearray()
        self.trailers: Optional[Headers] = None
        self.chunked: Optional[ChunkedDecoder] = None

    def feed(self, data: bytes):
        self.buffer.extend(data)

    def signal_eof(self):
        self.closed_eof = True

    def try_parse(self):
        if self.stage == "head":
            idx = self.buffer.find(b"\r\n\r\n")

            if idx == -1:
                if len(self.buffer) > MAX_HEADER_SIZE:
                    raise HTTPReportedViolationError(431, "Request Header Fields Too Large")
                return None

            head = bytes(self.buffer[:idx])
            del self.buffer[:idx + 4]

            text = head.decode("latin-1")
            line, _, rest = text.partition("\r\n")
            self.first_line = line
            self.headers = Headers.parse(rest) if rest else Headers([])

            status_code = None
            if self.is_response:
                parts = line.split(" ", 2)
                if len(parts) >= 2 and parts[1].isdigit():
                    status_code = int(parts[1])

            self.body_mode, self.content_length = find_body_mode(self.headers, is_response=self.is_response, status_code=status_code)

            if self.body_mode == "chunked":
                self.chunked = ChunkedDecoder()

            self.stage = "body"

        if self.stage == "body":
            if self.body_mode == "none":
                pass

            elif self.body_mode == "length":
                if len(self.buffer) < self.content_length:
                    return None

                if self.content_length > MAX_BODY_SIZE:
                    raise HTTPReportedViolationError(413, "Payload Too Large")

                self.body = bytearray(self.buffer[:self.content_length])
                del self.buffer[:self.content_length]

            elif self.body_mode == "chunked":
                if not self.chunked.feed(self.buffer):
                    return None

                if len(self.chunked.body) > MAX_BODY_SIZE:
                    raise HTTPReportedViolationError(413, "Payload Too Large")

                self.body = self.chunked.body

                if self.chunked.trailer_buffer:
                    self.trailers = Headers.parse(self.chunked.trailer_buffer.decode("latin-1") + "\r\n")

            elif self.body_mode == "close":
                if not self.closed_eof:
                    return None

                self.body = bytearray(self.buffer)
                self.buffer.clear()

            self.stage = "done"

        result = (self.first_line, self.headers, bytes(self.body), self.trailers)
        self.reset()
        return result

class Connection:
    def __init__(self, src: tuple[str, int], dst: tuple[str, int]):
        self.dst = dst
        self.src = src

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.receive_queue: asyncio.Queue = asyncio.Queue()
        self.reader_task: Optional[asyncio.Task] = None
        self.closed = False
        self.buffer = bytearray()
        self.eof = False

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(host=self.dst[0], port=self.dst[1])
        self.reader_task = asyncio.create_task(self.pump())

    async def pump(self):
        try:
            while True:
                data = await self.reader.read(65536)
                await self.receive_queue.put(data)
                if not data:
                    break
        except (ConnectionResetError, OSError):
            await self.receive_queue.put(b"")

    async def close(self, half_close: bool = False):
        if self.closed:
            return

        if half_close and self.writer is not None and self.writer.can_write_eof():
            try:
                self.writer.write_eof()
            except OSError:
                pass
            return

        if self.reader_task is not None:
            self.reader_task.cancel()

        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionResetError, OSError, BrokenPipeError):
                pass

        self.closed = True

    async def send(self, data: bytes):
        self.writer.write(data)
        await self.writer.drain()

    async def receive(self, n: int = -1) -> bytes:
        if n == -1:
            if self.buffer:
                data = bytes(self.buffer)
                self.buffer.clear()
                return data

            if self.eof:
                return b""

            chunk = await self.receive_queue.get()

            if not chunk:
                self.eof = True
                return b""

            return chunk

        while len(self.buffer) < n and not self.eof:
            chunk = await self.receive_queue.get()

            if not chunk:
                self.eof = True
                break

            self.buffer.extend(chunk)

        take = min(n, len(self.buffer))
        data = bytes(self.buffer[:take])
        del self.buffer[:take]
        return data

class Protocol(asyncio.Protocol):
    def __init__(self, src: Optional[tuple[str, int]] = None, handler: Optional["Handler"] = None, role: Role = Role.ORIGIN, upstream: Optional[tuple[str, int]] = None):
        self.src = src
        self.handler = handler
        self.role = role
        self.upstream = upstream
        self.connections: dict[tuple[str, int], Connection] = {}

        self.transport: Optional[asyncio.Transport] = None
        self.parser = MessageParser(is_response=False)
        self.client_addr: Optional[tuple] = None
        self.request_queue: asyncio.Queue = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self.upstream_connection: Optional[Connection] = None
        self.websocket: Optional[WebSocket] = None
        self.ws_queue: Optional[asyncio.Queue] = None
        self.tunnel_queue: Optional[asyncio.Queue] = None
        self.paused = False
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.tasks: set[asyncio.Task] = set()

    def track(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def connection_made(self, transport: asyncio.BaseTransport):
        self.transport = transport
        self.client_addr = transport.get_extra_info("peername")

        if self.src is None:
            self.src = self.client_addr

        self.worker_task = self.track(self.worker())

    def pause_writing(self):
        self.paused = True
        self.resume_event.clear()

    def resume_writing(self):
        self.paused = False
        self.resume_event.set()

    def data_received(self, data: bytes):
        if self.websocket is not None:
            self.ws_queue.put_nowait(data)
            return

        if self.tunnel_queue is not None:
            self.tunnel_queue.put_nowait(data)
            return

        self.parser.feed(data)

        try:
            while True:
                result = self.parser.try_parse()
                if result is None:
                    break
                self.request_queue.put_nowait(result)
        except HTTPError as exc:
            self.request_queue.put_nowait(exc)

    def eof_received(self) -> Optional[bool]:
        if self.tunnel_queue is not None:
            self.track(self.upstream_half_close())
            return True

        self.parser.signal_eof()
        return True

    async def upstream_half_close(self):
        if self.upstream_connection is not None:
            await self.upstream_connection.close(half_close=True)

    def connection_lost(self, exc: Optional[Exception]):
        if self.worker_task is not None:
            self.worker_task.cancel()

        if self.upstream_connection is not None:
            self.track(self.upstream_connection.close())

    async def await_writable(self):
        if self.paused:
            await self.resume_event.wait()

    async def write(self, data: bytes):
        await self.await_writable()
        self.transport.write(data)

    async def worker(self):
        while True:
            item = await self.request_queue.get()

            if isinstance(item, HTTPError):
                await self.write_error_response(item)
                self.transport.close()
                return

            first_line, headers, body, trailers = item

            try:
                keep_alive = await self.handle_request(first_line, headers, body, trailers)
            except HTTPError as exc:
                await self.write_error_response(exc)
                self.transport.close()
                return
            except Exception:
                await self.write_error_response(HTTPError(500, "Internal Server Error"))
                self.transport.close()
                return

            if self.websocket is not None or self.tunnel_queue is not None:
                return

            if not keep_alive:
                self.transport.close()
                return

    async def write_error_response(self, exc: HTTPError):
        body = exc.message.encode()
        reason = REASON_PHRASES.get(exc.status_code, "Error")
        head = (
            f"HTTP/1.1 {exc.status_code} {reason}\r\n"
            f"Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("latin-1")
        await self.write(head + body)

    def is_websocket_upgrade(self, request: Request) -> bool:
        upgrade = request.headers.get("Upgrade", "")
        connection_tokens = CommaHeader(request.headers.get("Connection", ""))
        has_upgrade_token = any(t.lower() == "upgrade" for t in connection_tokens.raw)
        return (upgrade.lower() == "websocket" and has_upgrade_token and request.headers.get("Sec-WebSocket-Key") is not None)

    async def do_websocket_upgrade(self, request: Request):
        version = request.headers.get("Sec-WebSocket-Version")
        key = request.headers.get("Sec-WebSocket-Key")

        if version != "13" or not key:
            raise HTTPReportedViolationError(426, "Upgrade Required")

        try:
            decoded_key = base64.b64decode(key)
        except Exception:
            raise HTTPReportedViolationError(400, "Invalid Sec-WebSocket-Key")

        if len(decoded_key) != 16:
            raise HTTPReportedViolationError(400, "Invalid Sec-WebSocket-Key")

        accept_key = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")

        response_head = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key}\r\n"
            "\r\n"
        ).encode("latin-1")

        await self.write(response_head)

        self.ws_queue = asyncio.Queue()
        self.websocket = WebSocket(transport=self.transport, feed=self.ws_queue, is_client=False)
        self.track(self.handler.on_websocket(self.websocket))

    async def handle_request(self, first_line: str, headers: Headers, body: bytes, trailers: Optional[Headers]) -> bool:
        parts = first_line.split(" ")

        if len(parts) != 3:
            raise HTTPReportedViolationError(400, "Malformed request line")

        method, target, version_token = parts

        if version_token not in ("HTTP/1.0", "HTTP/1.1"):
            raise HTTPReportedViolationError(505, "HTTP Version Not Supported")

        if method not in ("GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"):
            raise HTTPReportedViolationError(501, "Not Implemented")

        client_addr = self.client_addr or ("", 0)

        request = Request(
            method=method,
            target=target,
            protocol=version_token,
            headers=headers,
            trailers=trailers,
            body=body,
            client=client_addr
        )

        try:
            await finalize_request(request, strict=True)
        except HTTPViolationError as exc:
            raise HTTPReportedViolationError(400, str(exc))

        if self.role == Role.TUNNEL:
            if method != "CONNECT":
                raise HTTPReportedViolationError(400, "TUNNEL role only supports CONNECT")
            return await self.handle_connect(request)

        if method == "CONNECT":
            raise HTTPReportedViolationError(405, "Method Not Allowed")

        if self.role == Role.ORIGIN:
            if self.is_websocket_upgrade(request) and self.handler and self.handler.on_websocket:
                await self.do_websocket_upgrade(request)
                return True

            if self.handler and self.handler.on_request:
                response = await self.handler.on_request(request)
            else:
                response = Response(status_code=501, body=b"Not Implemented", headers=Headers({}))
        else:
            response = await self.dispatch_proxy(request)

        return await self.send_response(request, response)

    async def dispatch_proxy(self, request: Request) -> Response:
        if self.role == Role.GATEWAY:
            target = self.upstream
        else:
            target = (request.url.host, request.url.port or 80)

        if not target or not target[0]:
            raise HTTPReportedViolationError(400, "Cannot determine upstream target")

        connection = Connection(src=self.src, dst=target)

        try:
            await connection.connect()
        except OSError:
            return Response(status_code=502, body=b"Bad Gateway", headers=Headers({}))

        try:
            connection_header = CommaHeader(request.headers.get("Connection", ""))

            for extra in list(connection_header.raw):
                request.headers.remove(extra)

            for name in list(HOP_BY_HOP_HEADERS):
                request.headers.remove(name)

            path = request.url.path or "/"
            if request.url.query:
                path += f"?{request.url.query}"

            request_line = f"{request.method} {path} {request.protocol}\r\n"
            raw = request_line.encode("latin-1") + request.headers.build().encode("latin-1") + b"\r\n"

            if isinstance(request.body, bytes):
                raw += request.body

            await connection.send(raw)

            parser = MessageParser(is_response=True)
            result = None

            while True:
                chunk = await connection.receive()

                if not chunk:
                    parser.signal_eof()
                else:
                    parser.feed(chunk)

                result = parser.try_parse()

                if result is not None:
                    break

                if not chunk:
                    break

            if result is None:
                return Response(status_code=502, body=b"Bad Gateway", headers=Headers({}))

            first_line, resp_headers, resp_body, resp_trailers = result
            parts = first_line.split(" ", 2)
            status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 502

            for name in list(HOP_BY_HOP_HEADERS):
                resp_headers.remove(name)

            return Response(status_code=status_code, headers=resp_headers, body=resp_body, trailers=resp_trailers)

        finally:
            await connection.close()

    async def handle_connect(self, request: Request) -> bool:
        host, _, port_str = request.target.partition(":")

        try:
            port = int(port_str)
        except ValueError:
            raise HTTPReportedViolationError(400, "Invalid CONNECT target")

        connection = Connection(src=self.src, dst=(host, port))

        try:
            await connection.connect()
        except OSError:
            await self.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return False

        await self.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        self.upstream_connection = connection
        self.tunnel_queue = asyncio.Queue()

        async def client_to_upstream():
            try:
                while True:
                    data = await self.tunnel_queue.get()
                    if not data:
                        break
                    await connection.send(data)
            except (ConnectionResetError, OSError):
                pass
            finally:
                await connection.close(half_close=True)

        async def upstream_to_client():
            try:
                while True:
                    data = await connection.receive()
                    if not data:
                        break
                    await self.write(data)
            except (ConnectionResetError, OSError):
                pass
            finally:
                if self.transport.can_write_eof():
                    try:
                        self.transport.write_eof()
                    except OSError:
                        pass

        self.track(client_to_upstream())
        self.track(upstream_to_client())

        return True

    def should_keep_alive(self, request: Request) -> bool:
        connection_tokens = CommaHeader(request.headers.get("Connection", ""))
        request_wants_close = any(t.lower() == "close" for t in connection_tokens.raw)
        request_wants_keep_alive = any(t.lower() == "keep-alive" for t in connection_tokens.raw)

        if request_wants_close:
            return False

        if request.protocol == "HTTP/1.1":
            return True

        return request_wants_keep_alive

    async def send_response(self, request: Request, response: Response) -> bool:
        accept_encoding = request.headers.get("Accept-Encoding")

        if response.compression and not response.compressed and accept_encoding and isinstance(response.body, bytes):
            preference = ["zstd", "br", "gzip", "deflate"]
            accept = AcceptEncoding.parse(accept_encoding)
            acceptable = {c for c, q in accept.raw if q > 0}
            wildcard_ok = any(c == "*" and q > 0 for c, q in accept.raw)

            best = next((c for c in preference if c in acceptable or (wildcard_ok and c not in {c2 for c2, q in accept.raw if q == 0})), None)

            if best is not None:
                response.compress([best])

        keep_alive = self.should_keep_alive(request)
        response.protocol = request.protocol

        await finalize_response(response, strict=False, role=self.role)

        if response.headers.get("Connection", "").lower() == "close":
            keep_alive = False

        response.headers.set("Connection", "keep-alive" if keep_alive else "close")

        reason = REASON_PHRASES.get(response.status_code, "")
        status_line = f"{response.protocol} {response.status_code} {reason}\r\n"
        head = (status_line + response.headers.build() + "\r\n").encode("latin-1")

        await self.write(head)

        if request.method != "HEAD":
            await self.write_body(response)

        return keep_alive

    async def write_body(self, response: Response):
        body = response.body

        if body is None:
            return

        if isinstance(body, bytes):
            await self.write(body)
            return

        if isinstance(body, (os.PathLike, str)):
            path = body if isinstance(body, str) else os.fspath(body)
            start = 0
            remaining: Optional[int] = None

            if response.range is not None:
                start, end = response.range
                remaining = end - start + 1

            def open_and_seek():
                f = open(path, "rb")
                if start:
                    f.seek(start)
                return f

            f = await asyncio.to_thread(open_and_seek)

            try:
                while True:
                    read_size = 65536 if remaining is None else min(65536, remaining)

                    if read_size <= 0:
                        break

                    chunk = await asyncio.to_thread(f.read, read_size)

                    if not chunk:
                        break

                    await self.write(chunk)

                    if remaining is not None:
                        remaining -= len(chunk)
            finally:
                await asyncio.to_thread(f.close)

            return

        async for chunk in body:
            frame = f"{len(chunk):x}\r\n".encode("latin-1") + chunk + b"\r\n"
            await self.write(frame)

        trailer_text = response.trailers.build() if response.trailers else ""
        await self.write(f"0\r\n{trailer_text}\r\n".encode("latin-1"))
