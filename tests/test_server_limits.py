import asyncio
import contextlib

import pytest

from momiji import Server, Listener, IPVersion, Handler, PlainTextResponse

async def on_request(request):
    return PlainTextResponse("ok")

@contextlib.asynccontextmanager
async def running_server(**kwargs):
    server = Server(handler=Handler(on_request=on_request), **kwargs)
    sock = Listener(ip_version=IPVersion.IPv4, port=0).bind()
    port = sock.getsockname()[1]

    task = asyncio.create_task(server.serve([sock]))

    try:
        yield port
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=2)

async def read_until_closed(reader: asyncio.StreamReader, timeout: float = 2.0) -> bytes:
    chunks = []
    with contextlib.suppress(asyncio.TimeoutError):
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)

async def test_idle_timeout_closes_connection_with_no_request():
    async with running_server(idle_timeout=0.2, shutdown_timeout=0.1) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            data = await asyncio.wait_for(reader.read(1), timeout=1)
            assert data == b""
        finally:
            writer.close()

async def test_request_timeout_closes_slowly_trickled_request():
    async with running_server(request_timeout=0.2, shutdown_timeout=0.1) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\n")
            await writer.drain()

            response = await read_until_closed(reader, timeout=1)
            assert b"408" in response
        finally:
            writer.close()

async def test_max_connections_rejects_excess_connections_with_503():
    async with running_server(max_connections=1, shutdown_timeout=0.1) as port:
        reader1, writer1 = await asyncio.open_connection("127.0.0.1", port)
        try:
            reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
            try:
                response = await read_until_closed(reader2, timeout=1)
                assert b"503" in response
            finally:
                writer2.close()
        finally:
            writer1.close()

async def test_rate_limit_returns_429_after_burst_is_exhausted():
    async with running_server(rate_limit=(0, 1), shutdown_timeout=0.1) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request)
            await writer.drain()

            first = await asyncio.wait_for(reader.read(4096), timeout=1)
            assert b"200" in first

            writer.write(request)
            await writer.drain()

            second = await read_until_closed(reader, timeout=1)
            assert b"429" in second
        finally:
            writer.close()
