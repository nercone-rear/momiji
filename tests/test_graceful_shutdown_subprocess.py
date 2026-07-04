import asyncio
import os
import signal
import socket
import subprocess
import sys
import textwrap

import pytest

SERVER_SCRIPT = textwrap.dedent("""
    import asyncio
    import sys

    from momiji import Server, Listener, IPVersion, Handler, PlainTextResponse

    async def on_request(request):
        if request.target == "/slow":
            await asyncio.sleep(1.0)
        return PlainTextResponse("ok")

    def main():
        port = int(sys.argv[1])
        server = Server(handler=Handler(on_request=on_request), shutdown_timeout=3.0)
        server.run([Listener(ip_version=IPVersion.IPv4, port=port)], workers=0)

    if __name__ == "__main__":
        main()
""")

def free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER_SCRIPT)
    return str(path)

async def test_sigterm_drains_in_flight_request_and_exits_promptly(server_script):
    port = free_port()
    proc = subprocess.Popen([sys.executable, server_script, str(port)])

    try:
        for _ in range(50):
            try:
                _, w = await asyncio.open_connection("127.0.0.1", port)
                w.close()
                break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("server did not come up")

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /slow HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        await asyncio.sleep(0.2)

        os.kill(proc.pid, signal.SIGTERM)

        # the listener should stop accepting new connections promptly
        await asyncio.sleep(0.3)
        with pytest.raises((ConnectionRefusedError, asyncio.TimeoutError, OSError)):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=1)

        # the in-flight request must still complete successfully (graceful drain)
        data = await asyncio.wait_for(reader.read(4096), timeout=5)
        assert b"200" in data
        writer.close()

        # the process must exit well within the shutdown_timeout, not hang forever
        exit_code = await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5)
        assert exit_code == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
