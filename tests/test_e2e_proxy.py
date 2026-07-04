import asyncio

from momiji.server import Handler
from momiji.models import Role
from momiji.responses import PlainTextResponse

from conftest import read_http_response, read_exactly


class TestGatewayRole:
    async def test_forwards_request_to_fixed_upstream_and_returns_response(self, make_server):
        async def upstream_handler(request):
            return PlainTextResponse(f"upstream saw {request.method} {request.url.path}")

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        gateway = await make_server(role=Role.GATEWAY, upstream=(upstream.host, upstream.port))

        reader, writer = await gateway.open_connection()
        writer.write(b"GET /foo HTTP/1.1\r\nHost: whatever.invalid\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 200 OK"
        assert body == b"upstream saw GET /foo"
        writer.close()

    async def test_request_body_forwarded_to_upstream(self, make_server):
        received = {}

        async def upstream_handler(request):
            received["body"] = request.body
            return PlainTextResponse("ok")

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        gateway = await make_server(role=Role.GATEWAY, upstream=(upstream.host, upstream.port))

        reader, writer = await gateway.open_connection()
        writer.write(b"POST / HTTP/1.1\r\nHost: whatever.invalid\r\nContent-Length: 5\r\n\r\nhello")
        await writer.drain()

        await read_http_response(reader)
        assert received["body"] == b"hello"
        writer.close()

    async def test_chunked_request_body_forwarded_with_correct_length(self, make_server):
        # The gateway decodes an incoming chunked body into plain bytes and
        # strips Transfer-Encoding (a hop-by-hop header) before forwarding.
        # It must therefore set an explicit Content-Length reflecting the
        # decoded body, otherwise the forwarded request has neither
        # Transfer-Encoding nor Content-Length despite carrying a body,
        # desynchronizing the upstream connection's framing (request
        # smuggling) and corrupting any pipelined request that follows.
        received = {}

        async def upstream_handler(request):
            received["body"] = request.body
            return PlainTextResponse("ok")

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        gateway = await make_server(role=Role.GATEWAY, upstream=(upstream.host, upstream.port))

        reader, writer = await gateway.open_connection()
        writer.write(
            b"POST / HTTP/1.1\r\nHost: whatever.invalid\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
            b"GET /next HTTP/1.1\r\nHost: whatever.invalid\r\n\r\n"
        )
        await writer.drain()

        await read_http_response(reader)
        assert received["body"] == b"hello"

        status_line, _, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 200 OK"
        assert body == b"ok"
        writer.close()

    async def test_head_request_response_not_stuck_waiting_for_body(self, make_server):
        # A response to a HEAD request may carry a Content-Length header
        # describing the body that *would* accompany a GET, but the actual
        # bytes are never sent. The response parser must know the request
        # was a HEAD and skip waiting for a body, or the gateway hangs.
        async def upstream_handler(request):
            # The upstream (itself a spec-compliant origin server) sets
            # Content-Length from the real body but omits the actual bytes
            # because the request method is HEAD - exactly as RFC 7230
            # requires, and exactly what trips up a response parser that
            # isn't aware of the original request method.
            return PlainTextResponse("hello world, this is the body")

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        gateway = await make_server(role=Role.GATEWAY, upstream=(upstream.host, upstream.port))

        reader, writer = await gateway.open_connection()
        writer.write(b"HEAD / HTTP/1.1\r\nHost: whatever.invalid\r\n\r\n")
        await writer.drain()

        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        assert b"200" in head
        writer.close()

    async def test_upstream_response_headers_forwarded(self, make_server):
        from momiji.models import Response
        from momiji.headers import Headers

        async def upstream_handler(request):
            return Response(status_code=201, body=b"created", headers=Headers([("X-Upstream", ["yes"])]))

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        gateway = await make_server(role=Role.GATEWAY, upstream=(upstream.host, upstream.port))

        reader, writer = await gateway.open_connection()
        writer.write(b"GET / HTTP/1.1\r\nHost: whatever.invalid\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 201 Created"
        assert headers["x-upstream"] == "yes"
        writer.close()

    async def test_unreachable_upstream_returns_502(self, make_server):
        # Nothing is listening on this port (bind-and-close to get a free,
        # guaranteed-unused port on this host without racing another test).
        probe = await asyncio.get_running_loop().create_server(lambda: asyncio.Protocol(), host="127.0.0.1", port=0)
        dead_host, dead_port = probe.sockets[0].getsockname()[:2]
        probe.close()
        await probe.wait_closed()

        gateway = await make_server(role=Role.GATEWAY, upstream=(dead_host, dead_port))

        reader, writer = await gateway.open_connection()
        writer.write(b"GET / HTTP/1.1\r\nHost: whatever.invalid\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 502 Bad Gateway"
        writer.close()


class TestProxyRole:
    async def test_absolute_form_target_dispatches_to_embedded_host(self, make_server):
        async def upstream_handler(request):
            return PlainTextResponse(f"upstream saw {request.url.path}")

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        proxy = await make_server(role=Role.PROXY)

        reader, writer = await proxy.open_connection()
        target = f"http://{upstream.host}:{upstream.port}/proxied-path"
        writer.write(f"GET {target} HTTP/1.1\r\nHost: {upstream.host}:{upstream.port}\r\n\r\n".encode())
        await writer.drain()

        status_line, _, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 200 OK"
        assert body == b"upstream saw /proxied-path"
        writer.close()

    async def test_hop_by_hop_headers_stripped_from_forwarded_request(self, make_server):
        seen_headers = {}

        async def upstream_handler(request):
            seen_headers.update({k.lower(): v for k, v in request.headers.items()})
            return PlainTextResponse("ok")

        upstream = await make_server(handler=Handler(on_request=upstream_handler))
        proxy = await make_server(role=Role.PROXY)

        reader, writer = await proxy.open_connection()
        target = f"http://{upstream.host}:{upstream.port}/"
        writer.write(
            f"GET {target} HTTP/1.1\r\nHost: {upstream.host}:{upstream.port}\r\n"
            f"Connection: keep-alive, X-Hop\r\nX-Hop: should-be-stripped\r\nX-Keep: keep-me\r\n\r\n".encode()
        )
        await writer.drain()

        await read_http_response(reader)
        assert "x-hop" not in seen_headers
        assert "connection" not in seen_headers
        assert seen_headers.get("x-keep") == "keep-me"
        writer.close()


class TestConnectTunnel:
    async def test_connect_establishes_tunnel_and_relays_bytes(self, make_server):
        # A raw (non-HTTP) TCP echo server stands in for an arbitrary
        # CONNECT target, exercising the tunnel's raw byte relay in both
        # directions.
        async def echo(reader, writer):
            try:
                while True:
                    data = await reader.read(4096)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()

        echo_server = await asyncio.start_server(echo, host="127.0.0.1", port=0)
        echo_host, echo_port = echo_server.sockets[0].getsockname()[:2]

        tunnel = await make_server(role=Role.TUNNEL)
        reader, writer = await tunnel.open_connection()

        writer.write(f"CONNECT {echo_host}:{echo_port} HTTP/1.1\r\nHost: {echo_host}:{echo_port}\r\n\r\n".encode())
        await writer.drain()

        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        assert head == b"HTTP/1.1 200 Connection Established\r\n\r\n"

        writer.write(b"hello through the tunnel")
        await writer.drain()

        echoed = await read_exactly(reader, len(b"hello through the tunnel"))
        assert echoed == b"hello through the tunnel"

        writer.close()
        echo_server.close()
        await echo_server.wait_closed()

    async def test_connect_to_unreachable_target_returns_502(self, make_server):
        probe = await asyncio.get_running_loop().create_server(lambda: asyncio.Protocol(), host="127.0.0.1", port=0)
        dead_host, dead_port = probe.sockets[0].getsockname()[:2]
        probe.close()
        await probe.wait_closed()

        tunnel = await make_server(role=Role.TUNNEL)
        reader, writer = await tunnel.open_connection()

        writer.write(f"CONNECT {dead_host}:{dead_port} HTTP/1.1\r\nHost: {dead_host}:{dead_port}\r\n\r\n".encode())
        await writer.drain()

        data = await asyncio.wait_for(reader.read(), timeout=5)
        assert b"502" in data
        writer.close()

    async def test_connect_to_bracketed_ipv6_target_establishes_tunnel(self, make_server):
        # CONNECT authority-form targets an IPv6 literal in bracket notation
        # (e.g. "[::1]:PORT"); a naive `partition(":")` on the target would
        # split inside the brackets and always fail with 400.
        async def echo(reader, writer):
            try:
                while True:
                    data = await reader.read(4096)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()

        echo_server = await asyncio.start_server(echo, host="::1", port=0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        tunnel = await make_server(role=Role.TUNNEL)
        reader, writer = await tunnel.open_connection()

        writer.write(f"CONNECT [::1]:{echo_port} HTTP/1.1\r\nHost: [::1]:{echo_port}\r\n\r\n".encode())
        await writer.drain()

        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        assert head == b"HTTP/1.1 200 Connection Established\r\n\r\n"

        writer.write(b"hello ipv6")
        await writer.drain()

        echoed = await read_exactly(reader, len(b"hello ipv6"))
        assert echoed == b"hello ipv6"

        writer.close()
        echo_server.close()
        await echo_server.wait_closed()

    async def test_non_connect_method_rejected_on_tunnel_role(self, make_server):
        tunnel = await make_server(role=Role.TUNNEL)
        reader, writer = await tunnel.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()
