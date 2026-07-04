import asyncio

from momiji.websocket import WebSocket, OPCODE_CLOSE, OPCODE_PING


class FakeTransport:
    def __init__(self):
        self.written = bytearray()

    def write(self, data: bytes):
        self.written.extend(data)


def make_ws() -> tuple[WebSocket, FakeTransport]:
    transport = FakeTransport()
    ws = WebSocket(transport=transport, feed=asyncio.Queue(), is_client=False)
    return ws, transport


def parse_frame(data: bytes) -> tuple[int, bytes]:
    opcode = data[0] & 0x0F
    length7 = data[1] & 0x7F
    offset = 2

    if length7 <= 125:
        payload_len = length7
    elif length7 == 126:
        payload_len = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
    else:
        payload_len = int.from_bytes(data[offset:offset + 8], "big")
        offset += 8

    return opcode, bytes(data[offset:offset + payload_len])


class TestControlFramePayloadLimit:
    # RFC 6455 5.5: all control frames MUST have a payload length of 125
    # bytes or less.
    def test_ping_with_oversized_payload_is_truncated(self):
        ws, transport = make_ws()
        ws.ping(b"x" * 1000)

        opcode, payload = parse_frame(bytes(transport.written))
        assert opcode == OPCODE_PING
        assert len(payload) <= 125

    def test_ping_with_small_payload_is_unmodified(self):
        ws, transport = make_ws()
        ws.ping(b"hello")

        opcode, payload = parse_frame(bytes(transport.written))
        assert payload == b"hello"

    def test_close_with_oversized_reason_is_truncated(self):
        ws, transport = make_ws()
        ws.close(code=1000, reason="x" * 1000)

        opcode, payload = parse_frame(bytes(transport.written))
        assert opcode == OPCODE_CLOSE
        assert len(payload) <= 125

    def test_close_with_small_reason_round_trips(self):
        ws, transport = make_ws()
        ws.close(code=1000, reason="bye")

        opcode, payload = parse_frame(bytes(transport.written))
        assert int.from_bytes(payload[:2], "big") == 1000
        assert payload[2:] == b"bye"
