import asyncio

from momiji import Server

class FakeProtocol:
    def __init__(self, tracker, handling_request):
        self.tracker = tracker
        self.handling_request = handling_request
        self.close_called = False
        self.transport = self

    def close(self):
        self.close_called = True
        self.tracker.release(self)

async def test_drain_closes_idle_connections_immediately():
    server = Server(shutdown_timeout=5)
    idle = FakeProtocol(server.tracker, handling_request=False)
    server.tracker.active.add(idle)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(server.drain(), timeout=1)
    elapsed = loop.time() - start

    assert idle.close_called is True
    assert elapsed < 1
    assert server.tracker.shutting_down is True

async def test_drain_waits_for_active_connections_then_forces_close():
    server = Server(shutdown_timeout=0.2)
    active = FakeProtocol(server.tracker, handling_request=True)
    server.tracker.active.add(active)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await server.drain()
    elapsed = loop.time() - start

    assert active.close_called is True
    assert elapsed >= 0.2

async def test_drain_stops_waiting_early_once_connection_finishes_on_its_own():
    server = Server(shutdown_timeout=5)
    active = FakeProtocol(server.tracker, handling_request=True)
    server.tracker.active.add(active)

    async def finish_soon():
        await asyncio.sleep(0.1)
        server.tracker.release(active)

    asyncio.create_task(finish_soon())

    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(server.drain(), timeout=1)
    elapsed = loop.time() - start

    assert elapsed < 1
    assert active.close_called is False
