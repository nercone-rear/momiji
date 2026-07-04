from __future__ import annotations

import asyncio
from typing import Callable, Optional

import pytest_asyncio

from momiji.models import Role
from momiji.protocol import Protocol
from momiji.server import Handler
from momiji.limits import ConnectionTracker, RateLimiter


class RunningServer:
    """A raw asyncio.Server wrapping momiji's Protocol, bound to 127.0.0.1:0.

    Bypasses momiji.server.Server (which installs process-wide signal handlers
    and forks workers) so tests can start/stop many servers cheaply within a
    single process.
    """

    def __init__(self, server: asyncio.AbstractServer, host: str, port: int, tracker: Optional[ConnectionTracker]):
        self.server = server
        self.host = host
        self.port = port
        self.tracker = tracker

    async def open_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection(self.host, self.port)

    async def aclose(self):
        if self.tracker is not None:
            for protocol in list(self.tracker.active):
                protocol.transport.close()

        self.server.close()
        await self.server.wait_closed()


@pytest_asyncio.fixture
async def make_server():
    created: list[RunningServer] = []

    async def _make(
        *,
        handler: Optional[Handler] = None,
        role: Role = Role.ORIGIN,
        upstream: Optional[tuple[str, int]] = None,
        max_connections: Optional[int] = None,
        rate_limit: Optional[tuple[float, float]] = None,
        idle_timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> RunningServer:
        loop = asyncio.get_running_loop()

        tracker = ConnectionTracker(max_connections)
        rate_limiter = RateLimiter(*rate_limit) if rate_limit is not None else None

        def factory():
            return Protocol(
                handler=handler or Handler(),
                role=role,
                upstream=upstream,
                tracker=tracker,
                rate_limiter=rate_limiter,
                idle_timeout=idle_timeout,
                request_timeout=request_timeout,
            )

        server = await loop.create_server(factory, host="127.0.0.1", port=0)
        host, port = server.sockets[0].getsockname()[:2]

        running = RunningServer(server, host, port, tracker)
        created.append(running)
        return running

    yield _make

    for running in created:
        await running.aclose()


async def read_exactly(reader: asyncio.StreamReader, n: int) -> bytes:
    return await asyncio.wait_for(reader.readexactly(n), timeout=5)


async def read_until(reader: asyncio.StreamReader, sep: bytes) -> bytes:
    return await asyncio.wait_for(reader.readuntil(sep), timeout=5)


def build_client_ws_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """Builds a masked (client-to-server) WebSocket frame, per RFC 6455 5.2."""

    import os as _os

    header = bytearray()
    header.append((0x80 if fin else 0x00) | (opcode & 0x0F))

    length = len(payload)
    mask_bit = 0x80

    if length <= 125:
        header.append(mask_bit | length)
    elif length <= 0xFFFF:
        header.append(mask_bit | 126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(mask_bit | 127)
        header.extend(length.to_bytes(8, "big"))

    mask_key = _os.urandom(4)
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    return bytes(header) + masked


async def read_server_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes, bool]:
    """Reads one unmasked (server-to-client) WebSocket frame."""

    byte0, byte1 = await read_exactly(reader, 2)

    fin = bool(byte0 & 0x80)
    opcode = byte0 & 0x0F
    masked = bool(byte1 & 0x80)
    length7 = byte1 & 0x7F

    assert not masked, "server-to-client frames must not be masked"

    if length7 <= 125:
        payload_len = length7
    elif length7 == 126:
        payload_len = int.from_bytes(await read_exactly(reader, 2), "big")
    else:
        payload_len = int.from_bytes(await read_exactly(reader, 8), "big")

    payload = await read_exactly(reader, payload_len)
    return opcode, payload, fin


async def read_http_response(reader: asyncio.StreamReader) -> tuple[str, dict, bytes]:
    """Reads a single, non-chunked-or-close HTTP/1.x response head + body.

    Returns (status_line, headers_dict_lowercased, body). Understands both
    Content-Length and chunked transfer-encoding bodies. Raises on Connection:
    close bodies (read the socket manually for those cases instead).
    """

    head = await read_until(reader, b"\r\n\r\n")
    head_text = head[:-4].decode("latin-1")
    lines = head_text.split("\r\n")
    status_line = lines[0]

    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()

    body = b""

    if "transfer-encoding" in headers and "chunked" in headers["transfer-encoding"].lower():
        while True:
            size_line = await read_until(reader, b"\r\n")
            size = int(size_line.strip(), 16)

            if size == 0:
                # last-chunk CRLF [trailer-part] CRLF: read trailer lines
                # (each already CRLF-terminated) until the blank line that
                # ends the trailer section.
                while (await read_until(reader, b"\r\n")) != b"\r\n":
                    pass
                break

            chunk = await read_exactly(reader, size)
            await read_exactly(reader, 2)  # trailing CRLF
            body += chunk

    elif "content-length" in headers:
        length = int(headers["content-length"])
        if length > 0:
            body = await read_exactly(reader, length)

    return status_line, headers, body
