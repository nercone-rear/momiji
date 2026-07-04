import os
import signal
import socket
import uvloop
import asyncio
from enum import Enum
from typing import Optional, Callable
from dataclasses import dataclass

from .models import Role
from .protocol import Protocol
from .limits import ConnectionTracker, RateLimiter

class IPVersion(Enum):
    IPv4 = socket.AF_INET
    IPv6 = socket.AF_INET6

@dataclass(frozen=True)
class Listener:
    ip_version: Optional[IPVersion] = None
    port: Optional[int] = None
    path: Optional[str] = None

    def __post_init__(self):
        if (self.port is None) == (self.path is None):
            raise ValueError("specify exactly one of port or path")

        if self.port is not None and self.ip_version is None:
            raise ValueError("ip_version is required when port is specified")

        if self.path is not None and self.ip_version is not None:
            raise ValueError("ip_version must not be specified when path is specified")

    def bind(self, reuse_port: bool = False) -> socket.socket:
        if self.is_uds:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass

            sock.bind(self.path)
        else:
            sock = socket.socket(self.ip_version.value, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if reuse_port:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

            address = "" if self.ip_version is IPVersion.IPv4 else "::"
            sock.bind((address, self.port))

        sock.listen()
        sock.setblocking(False)

        return sock

    @property
    def is_uds(self) -> bool:
        return self.path is not None

class Handler:
    def __init__(self, on_request: Optional[Callable] = None, on_websocket: Optional[Callable] = None):
        self.on_request = on_request      # (request: Request) -> Response
        self.on_websocket = on_websocket  # (websocket: WebSocket) -> None

class Server:
    def __init__(
        self,
        role: Role = Role.ORIGIN,
        handler: Optional[Handler] = None,
        upstream: Optional[tuple[str, int]] = None,
        *,
        max_connections: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
        rate_limit: Optional[tuple[float, float]] = None,
        shutdown_timeout: float = 30.0,
    ):
        self.role = role
        self.handler = handler or Handler()
        self.upstream = upstream
        self.idle_timeout = idle_timeout
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        self.tracker = ConnectionTracker(max_connections)
        self.rate_limiter = RateLimiter(*rate_limit) if rate_limit is not None else None

    def run(self, listeners: list[Listener], workers: int = 0):
        if workers < 0:
            raise ValueError("workers must be at least 0")

        if workers == 0:
            sockets = [listener.bind() for listener in listeners]
            self.run_worker(sockets)
            return

        shared_sockets = [listener.bind() for listener in listeners if listener.is_uds]
        tcp_listeners = [listener for listener in listeners if not listener.is_uds]

        pids = [self.fork_worker(shared_sockets, tcp_listeners) for _ in range(workers)]

        def forward_signal(signum, frame):
            for pid in pids:
                try:
                    os.kill(pid, signum)
                except ProcessLookupError:
                    pass

        signal.signal(signal.SIGTERM, forward_signal)
        signal.signal(signal.SIGINT, forward_signal)

        while pids:
            try:
                pid, _ = os.wait()
            except ChildProcessError:
                break
            except InterruptedError:
                continue
            else:
                pids.remove(pid)

    def fork_worker(self, shared_sockets: list[socket.socket], tcp_listeners: list[Listener]) -> int:
        pid = os.fork()

        if pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)

            own_sockets = [listener.bind(reuse_port=True) for listener in tcp_listeners]
            self.run_worker(shared_sockets + own_sockets)
            os._exit(0)

        return pid

    def run_worker(self, sockets: list[socket.socket]):
        uvloop.install()
        asyncio.run(self.serve(sockets))

    async def serve(self, sockets: list[socket.socket]):
        loop = asyncio.get_running_loop()

        def protocol_factory():
            return Protocol(
                src=None,
                handler=self.handler,
                role=self.role,
                upstream=self.upstream,
                tracker=self.tracker,
                rate_limiter=self.rate_limiter,
                idle_timeout=self.idle_timeout,
                request_timeout=self.request_timeout,
            )

        servers = [await loop.create_server(protocol_factory, sock=sock) for sock in sockets]
        serve_forever_task = asyncio.gather(*(server.serve_forever() for server in servers))

        prune_task = asyncio.create_task(self.prune_rate_limiter()) if self.rate_limiter is not None else None

        shutdown_event = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_event.set)

        shutdown_wait_task = asyncio.create_task(shutdown_event.wait())

        try:
            await asyncio.wait({serve_forever_task, shutdown_wait_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)

            if not shutdown_wait_task.done():
                shutdown_wait_task.cancel()

            if prune_task is not None:
                prune_task.cancel()

            for server in servers:
                server.close()

            # Server.serve_forever(), once cancelled, waits on Server.wait_closed() internally,
            # which in turn blocks until all active connections are gone. drain() must run first
            # to actually close them, or cancelling/awaiting serve_forever_task below deadlocks.
            #
            # server.close() alone is not enough to unblock serve_forever_task: asyncio's Server
            # cancels its own internal serve_forever future as part of close(), but uvloop's Server
            # does not, so serve_forever_task must be cancelled explicitly to work on both loops.
            await self.drain()

            serve_forever_task.cancel()

            try:
                await serve_forever_task
            except asyncio.CancelledError:
                pass

            await asyncio.gather(*(server.wait_closed() for server in servers))

    async def drain(self):
        self.tracker.shutting_down = True

        for protocol in list(self.tracker.active):
            if not protocol.handling_request:
                protocol.transport.close()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.shutdown_timeout

        while self.tracker.active and loop.time() < deadline:
            await asyncio.sleep(0.1)

        for protocol in list(self.tracker.active):
            protocol.transport.close()

    async def prune_rate_limiter(self):
        while True:
            await asyncio.sleep(60)
            self.rate_limiter.prune(300)
