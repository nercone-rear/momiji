import socket
from typing import Optional, Callable

from .models import Role

class Handler:
    def __init__(self, on_request: Optional[Callable] = None, on_websocket: Optional[Callable] = None):
        self.on_request = on_request  # (request: Request) -> Response
        self.on_websocket = on_websocket  # (websocket: WebSocket) -> None

class Server:
    def __init__(self, role: Role = Role.ORIGIN):
        self.role = role

    def run(self, sockets: list[socket.socket]):
        ...

    async def serve(self, sockets: list[socket.socket]):
        ...
