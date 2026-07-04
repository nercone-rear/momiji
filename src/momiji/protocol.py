from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .server import Handler

class Connection:
    def __init__(self, src: tuple[str, int], dst: tuple[str, int]):
        self.dst = dst
        self.src = src

    async def connect(self):
        ...

    async def close(self, half_close: bool = False):
        ...

    async def send(self, data: bytes):
        ...

    async def receive(self, n: int = -1) -> bytes:
        buffer = await self.receive_queue.get()
        return buffer if n == -1 else buffer[:n]

class Protocol(asyncio.Protocol):
    def __init__(self, src: Optional[tuple[str, int]] = None, handler: Optional[Handler] = None):
        self.src = src
        self.handler = handler
        self.connections: dict[tuple[str, int], Connection] = {}
    ...
