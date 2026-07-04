from __future__ import annotations

import asyncio
import base64
import hashlib

from momiji.server import Handler

from conftest import read_until, build_client_ws_frame, read_server_ws_frame

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


def make_ws_key() -> str:
    import os
    return base64.b64encode(os.urandom(16)).decode("ascii")


def expected_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


async def send_handshake(writer, key: str, *, version: str = "13", extra: str = ""):
    request = (
        f"GET /ws HTTP/1.1\r\n"
        f"Host: example.com\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: {version}\r\n"
        f"{extra}\r\n"
    )
    writer.write(request.encode("latin-1"))
    await writer.drain()


async def read_head(reader) -> tuple[str, dict]:
    head = await read_until(reader, b"\r\n\r\n")
    text = head[:-4].decode("latin-1")
    lines = text.split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return lines[0], headers


async def echo_handler(ws):
    # A real on_websocket handler owns protocol-error handling: momiji raises
    # WebSocketProtocolError out of read()/read_message() but does not itself
    # tear down the transport on every violation (see websocket.py's
    # next_frame(), which for reserved-bits/unmasked-frame violations raises
    # without routing through protocol_error()/close()). Mirror what a real
    # handler is expected to do.
    try:
        while True:
            msg = await ws.read()
            if not msg:
                break
            await ws.write(msg)
    except Exception:
        pass
    finally:
        try:
            ws.transport.close()
        except Exception:
            pass


def make_echo_server(make_server):
    return make_server(handler=Handler(on_websocket=echo_handler))


class TestHandshake:
    async def test_successful_upgrade_returns_101_with_correct_accept(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        key = make_ws_key()
        await send_handshake(writer, key)

        status_line, headers = await read_head(reader)
        assert status_line == "HTTP/1.1 101 Switching Protocols"
        assert headers["upgrade"] == "websocket"
        assert headers["connection"] == "Upgrade"
        assert headers["sec-websocket-accept"] == expected_accept(key)
        writer.close()

    async def test_missing_version_returns_426(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        writer.write(
            b"GET /ws HTTP/1.1\r\nHost: a\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: " + make_ws_key().encode() + b"\r\n\r\n"
        )
        await writer.drain()

        status_line, _ = await read_head(reader)
        assert status_line == "HTTP/1.1 426 Upgrade Required"
        writer.close()

    async def test_wrong_version_returns_426(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key(), version="8")

        status_line, _ = await read_head(reader)
        assert status_line == "HTTP/1.1 426 Upgrade Required"
        writer.close()

    async def test_malformed_key_returns_400(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        writer.write(
            b"GET /ws HTTP/1.1\r\nHost: a\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: not-valid-base64!!\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        await writer.drain()

        status_line, _ = await read_head(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()

    async def test_key_decoding_to_wrong_length_returns_400(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        short_key = base64.b64encode(b"short").decode("ascii")
        await send_handshake(writer, short_key)

        status_line, _ = await read_head(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()

    async def test_missing_upgrade_header_is_treated_as_regular_request(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        writer.write(b"GET /ws HTTP/1.1\r\nHost: a\r\nSec-WebSocket-Key: " + make_ws_key().encode() + b"\r\nSec-WebSocket-Version: 13\r\n\r\n")
        await writer.drain()

        status_line, _ = await read_head(reader)
        assert status_line == "HTTP/1.1 501 Not Implemented"
        writer.close()


class TestFraming:
    async def test_text_message_echoed(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        writer.write(build_client_ws_frame(OPCODE_TEXT, "hello".encode("utf-8")))
        await writer.drain()

        opcode, payload, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_TEXT
        assert payload == b"hello"
        assert fin
        writer.close()

    async def test_binary_message_echoed(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        writer.write(build_client_ws_frame(OPCODE_BINARY, b"\x00\x01\x02\xff"))
        await writer.drain()

        opcode, payload, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_BINARY
        assert payload == b"\x00\x01\x02\xff"
        writer.close()

    async def test_ping_is_answered_with_pong(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        writer.write(build_client_ws_frame(OPCODE_PING, b"ping-data"))
        await writer.drain()

        opcode, payload, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_PONG
        assert payload == b"ping-data"
        writer.close()

    async def test_fragmented_text_message_reassembled(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        writer.write(build_client_ws_frame(OPCODE_TEXT, b"hel", fin=False))
        writer.write(build_client_ws_frame(OPCODE_CONTINUATION, b"lo", fin=True))
        await writer.drain()

        opcode, payload, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_TEXT
        assert payload == b"hello"
        writer.close()

    async def test_close_handshake_completes_and_closes_connection(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        close_payload = (1000).to_bytes(2, "big") + b"bye"
        writer.write(build_client_ws_frame(OPCODE_CLOSE, close_payload))
        await writer.drain()

        opcode, payload, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_CLOSE

        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest == b""
        writer.close()

    async def test_large_message_uses_extended_length_encoding(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        payload = b"x" * 70000  # forces the 8-byte extended length form
        writer.write(build_client_ws_frame(OPCODE_BINARY, payload))
        await writer.drain()

        opcode, received, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_BINARY
        assert received == payload
        writer.close()

    async def test_medium_message_uses_16bit_length_encoding(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        payload = b"y" * 1000  # forces the 2-byte extended length form
        writer.write(build_client_ws_frame(OPCODE_BINARY, payload))
        await writer.drain()

        opcode, received, fin = await read_server_ws_frame(reader)
        assert opcode == OPCODE_BINARY
        assert received == payload
        writer.close()

    async def test_reserved_bits_set_is_rejected(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        frame = bytearray(build_client_ws_frame(OPCODE_TEXT, b"hi"))
        frame[0] |= 0x40  # set an RSV bit
        writer.write(bytes(frame))
        await writer.drain()

        # Server must not silently accept a reserved-bit frame as valid data;
        # the connection must be torn down rather than echoing it back.
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest != b"hi"
        assert rest == b""
        writer.close()

    async def test_unmasked_client_frame_is_rejected(self, make_server):
        server = await make_echo_server(make_server)
        reader, writer = await server.open_connection()

        await send_handshake(writer, make_ws_key())
        await read_head(reader)

        # 0x81 = FIN + text opcode, 0x02 = unmasked length-2 payload (no mask bit)
        writer.write(b"\x81\x02hi")
        await writer.drain()

        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest != b"hi"
        assert rest == b""
        writer.close()
