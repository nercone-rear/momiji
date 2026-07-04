import asyncio
from typing import Optional, TYPE_CHECKING

from .errors import WebSocketProtocolError

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA

MAX_MESSAGE_SIZE = 100 * 1024 * 1024

class WebSocket:
    def __init__(self, transport, feed: "asyncio.Queue", is_client: bool = False):
        self.transport = transport
        self.feed = feed
        self.is_client = is_client

        self.recv_buffer = bytearray()
        self.fragment_opcode: Optional[int] = None
        self.fragment_buffer = bytearray()
        self.closed = False
        self.close_sent = False
        self.leftover: Optional[bytes | str] = None

    def build_frame(self, opcode: int, payload: bytes, fin: bool = True) -> bytes:
        header = bytearray()
        header.append((0x80 if fin else 0x00) | (opcode & 0x0F))

        length = len(payload)

        if length <= 125:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(127)
            header.extend(length.to_bytes(8, "big"))

        return bytes(header) + payload

    def next_frame(self):
        buffer = self.recv_buffer

        if len(buffer) < 2:
            return None

        byte0 = buffer[0]
        byte1 = buffer[1]

        fin = bool(byte0 & 0x80)
        rsv = byte0 & 0x70
        opcode = byte0 & 0x0F

        if rsv != 0:
            raise WebSocketProtocolError(1002, "reserved bits set")

        masked = bool(byte1 & 0x80)
        length7 = byte1 & 0x7F

        if not masked:
            raise WebSocketProtocolError(1002, "client frames must be masked")

        offset = 2

        if length7 <= 125:
            payload_len = length7
        elif length7 == 126:
            if len(buffer) < offset + 2:
                return None
            payload_len = int.from_bytes(buffer[offset:offset + 2], "big")
            offset += 2
        else:
            if len(buffer) < offset + 8:
                return None
            payload_len = int.from_bytes(buffer[offset:offset + 8], "big")
            offset += 8
            if payload_len & (1 << 63):
                raise WebSocketProtocolError(1002, "invalid payload length")

        if payload_len > MAX_MESSAGE_SIZE:
            raise WebSocketProtocolError(1009, "message too large")

        if opcode in (OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG) and payload_len > 125:
            raise WebSocketProtocolError(1002, "control frame payload too large")

        if len(buffer) < offset + 4:
            return None

        mask_key = bytes(buffer[offset:offset + 4])
        offset += 4

        if len(buffer) < offset + payload_len:
            return None

        masked_payload = buffer[offset:offset + payload_len]
        unmasked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(masked_payload))

        del buffer[:offset + payload_len]

        return opcode, unmasked, fin

    def protocol_error(self, code: int, message: str):
        self.close(code=code, reason=message)
        self.closed = True
        raise WebSocketProtocolError(code, message)

    def send_frame(self, opcode: int, payload: bytes):
        self.transport.write(self.build_frame(opcode, payload, fin=True))

    async def read_message(self) -> bytes | str:
        while True:
            frame = self.next_frame()

            while frame is None:
                if self.closed:
                    return b""

                chunk = await self.feed.get()

                if not chunk:
                    self.closed = True
                    return b""

                self.recv_buffer.extend(chunk)
                frame = self.next_frame()

            opcode, payload, fin = frame

            if opcode in (OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG):
                if not fin:
                    self.protocol_error(1002, "control frame must not be fragmented")

                if opcode == OPCODE_CLOSE:
                    if len(payload) == 1:
                        self.protocol_error(1002, "close frame payload must be 0 or at least 2 bytes")

                    if not self.close_sent:
                        code = 1000
                        reason = b""

                        if len(payload) >= 2:
                            code = int.from_bytes(payload[:2], "big")
                            reason = payload[2:]

                        self.close(code=code, reason=reason.decode("utf-8", errors="replace"))

                    self.closed = True

                    try:
                        self.transport.close()
                    except Exception:
                        pass

                    return b""

                if opcode == OPCODE_PING:
                    self.send_frame(OPCODE_PONG, payload)

                continue

            if opcode in (OPCODE_TEXT, OPCODE_BINARY):
                if self.fragment_opcode is not None:
                    self.protocol_error(1002, "new data frame before previous fragmented message finished")

                self.fragment_opcode = opcode
                self.fragment_buffer = bytearray(payload)
            elif opcode == OPCODE_CONTINUATION:
                if self.fragment_opcode is None:
                    self.protocol_error(1002, "continuation frame without preceding data frame")

                self.fragment_buffer.extend(payload)
            else:
                self.protocol_error(1002, f"unsupported opcode {opcode}")

            if len(self.fragment_buffer) > MAX_MESSAGE_SIZE:
                self.protocol_error(1009, "message too large")

            if fin:
                message_opcode = self.fragment_opcode
                message_bytes = bytes(self.fragment_buffer)
                self.fragment_opcode = None
                self.fragment_buffer = bytearray()

                if message_opcode == OPCODE_TEXT:
                    try:
                        return message_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        self.protocol_error(1007, "invalid utf-8 in text message")

                return message_bytes

    async def read(self, size: int = -1) -> bytes | str:
        if self.leftover is not None:
            data = self.leftover
            self.leftover = None
        else:
            data = await self.read_message()

        if not data:
            return data

        if size < 0 or len(data) <= size:
            return data

        self.leftover = data[size:]
        return data[:size]

    async def write(self, data: bytes | str):
        if isinstance(data, str):
            opcode = OPCODE_TEXT
            payload = data.encode("utf-8")
        else:
            opcode = OPCODE_BINARY
            payload = data

        self.transport.write(self.build_frame(opcode, payload, fin=True))

    def ping(self, payload: bytes = b""):
        self.transport.write(self.build_frame(OPCODE_PING, payload[:125], fin=True))

    def close(self, code: int = 1000, reason: str = ""):
        if self.close_sent:
            return

        self.close_sent = True
        payload = code.to_bytes(2, "big") + reason.encode("utf-8")[:123]
        self.transport.write(self.build_frame(OPCODE_CLOSE, payload, fin=True))
