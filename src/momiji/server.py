import socket
import uvloop
import asyncio
from typing import Optional, Callable

from .models import Role
from .protocol import Protocol

class Handler:
    def __init__(self, on_request: Optional[Callable] = None, on_websocket: Optional[Callable] = None):
        self.on_request = on_request  # (request: Request) -> Response
        self.on_websocket = on_websocket  # (websocket: WebSocket) -> None

class Server:
    def __init__(self, role: Role = Role.ORIGIN, handler: Optional[Handler] = None, upstream: Optional[tuple[str, int]] = None):
        self.role = role
        self.handler = handler or Handler()
        self.upstream = upstream

    def run(self, sockets: list[socket.socket]):
        uvloop.install()
        asyncio.run(self.serve(sockets))

    async def serve(self, sockets: list[socket.socket]):
        loop = asyncio.get_running_loop()

        def protocol_factory():
            return Protocol(src=None, handler=self.handler, role=self.role, upstream=self.upstream)

        servers = [await loop.create_server(protocol_factory, sock=sock) for sock in sockets]

        try:
            await asyncio.gather(*(server.serve_forever() for server in servers))
        finally:
            for server in servers:
                server.close()

            await asyncio.gather(*(server.wait_closed() for server in servers))
