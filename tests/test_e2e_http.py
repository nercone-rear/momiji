from __future__ import annotations

import asyncio
import gzip
import zlib

import brotlicffi
import zstandard
import minify_html
import pytest

from momiji.server import Handler
from momiji.models import Response
from momiji.headers import Headers
from momiji.responses import PlainTextResponse, HTMLResponse, FileResponse

from conftest import read_http_response, read_until, read_exactly


def handler_returning(response_factory):
    async def on_request(request):
        return response_factory(request)

    return Handler(on_request=on_request)


class TestBasicRequestResponse:
    async def test_get_returns_200_with_body(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("hello")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)

        assert status_line == "HTTP/1.1 200 OK"
        assert body == b"hello"
        assert headers["content-type"] == "text/plain"
        writer.close()

    async def test_head_request_has_no_body(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("hello")))
        reader, writer = await server.open_connection()

        writer.write(b"HEAD / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()

        head = await read_until(reader, b"\r\n\r\n")
        assert b"200" in head
        assert b"Content-Length: 5" in head

        # No body should follow; a subsequent pipelined request should be
        # readable immediately since nothing else was written to the socket.
        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        status_line, _, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 200 OK"
        assert body == b"hello"
        writer.close()

    async def test_custom_status_code_and_body(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: Response(status_code=404, body=b"not found", headers=Headers([("Content-Type", ["text/plain"])]))))
        reader, writer = await server.open_connection()

        writer.write(b"GET /missing HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 404 Not Found"
        assert body == b"not found"
        writer.close()

    async def test_203_status_gets_correct_reason_phrase(self, make_server):
        # 203 Non-Authoritative Information must not collide with 103 Early
        # Hints in the server's reason-phrase table.
        server = await make_server(handler=handler_returning(lambda req: Response(status_code=203, body=b"ok")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 203 Non-Authoritative Information"
        writer.close()

    async def test_no_handler_returns_501(self, make_server):
        server = await make_server(handler=None)
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()

        status_line, _, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 501 Not Implemented"
        writer.close()

    async def test_request_body_delivered_to_handler(self, make_server):
        received = {}

        async def on_request(request):
            received["body"] = request.body
            return PlainTextResponse("ok")

        server = await make_server(handler=Handler(on_request=on_request))
        reader, writer = await server.open_connection()

        writer.write(b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 5\r\n\r\nhello")
        await writer.drain()

        await read_http_response(reader)
        assert received["body"] == b"hello"
        writer.close()

    async def test_chunked_request_body_decoded_for_handler(self, make_server):
        received = {}

        async def on_request(request):
            received["body"] = request.body
            return PlainTextResponse("ok")

        server = await make_server(handler=Handler(on_request=on_request))
        reader, writer = await server.open_connection()

        writer.write(
            b"POST / HTTP/1.1\r\nHost: example.com\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"3\r\nfoo\r\n3\r\nbar\r\n0\r\n\r\n"
        )
        await writer.drain()

        await read_http_response(reader)
        assert received["body"] == b"foobar"
        writer.close()

    async def test_streaming_response_uses_chunked_encoding(self, make_server):
        async def gen():
            yield b"foo"
            yield b"bar"

        async def on_request(request):
            return Response(body=gen())

        server = await make_server(handler=Handler(on_request=on_request))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert headers["transfer-encoding"] == "chunked"
        assert body == b"foobar"
        writer.close()

    async def test_pipelined_requests_get_sequential_responses(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse(req.url.path)))
        reader, writer = await server.open_connection()

        writer.write(
            b"GET /a HTTP/1.1\r\nHost: example.com\r\n\r\n"
            b"GET /b HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        await writer.drain()

        _, _, body1 = await read_http_response(reader)
        _, _, body2 = await read_http_response(reader)
        assert body1 == b"/a"
        assert body2 == b"/b"
        writer.close()


class TestConnectionLifecycle:
    async def test_http11_defaults_to_keep_alive(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        _, headers, _ = await read_http_response(reader)
        assert headers["connection"] == "keep-alive"
        writer.close()

    async def test_connection_close_header_closes_after_response(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert headers["connection"] == "close"

        # server must close the connection: further reads hit EOF
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest == b""
        writer.close()

    async def test_http10_defaults_to_close(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.0\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert status_line == "HTTP/1.0 200 OK"
        assert headers["connection"] == "close"
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest == b""
        writer.close()

    async def test_http10_keep_alive_honored(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.0\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()

        _, headers, _ = await read_http_response(reader)
        assert headers["connection"] == "keep-alive"

        # connection must still be usable
        writer.write(b"GET / HTTP/1.0\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()
        status_line, _, _ = await read_http_response(reader)
        assert status_line == "HTTP/1.0 200 OK"
        writer.close()


class TestProtocolViolations:
    async def test_malformed_request_line_returns_400(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(b"GARBAGE\r\nHost: a\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()

    async def test_missing_host_header_http11_returns_400(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()

    async def test_conflicting_transfer_encoding_and_content_length_returns_400(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(
            b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\nContent-Length: 5\r\n\r\n"
        )
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()

    async def test_obsolete_line_folding_returns_400(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\n X-Folded: bad\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 400 Bad Request"
        writer.close()

    async def test_unknown_method_returns_501(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(b"FROBNICATE / HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 501 Not Implemented"
        writer.close()

    async def test_unsupported_http_version_returns_505(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/2.0\r\nHost: a\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 505 HTTP Version Not Supported"
        writer.close()

    async def test_oversized_headers_return_431(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        # Deliberately withhold the terminating blank line so the header
        # block is still incomplete once it exceeds the 64 KiB limit --
        # this is the case explicitly size-checked while streaming in.
        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nX-Big: " + b"a" * (70 * 1024))
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 431 Request Header Fields Too Large"
        writer.close()

    async def test_connect_method_on_origin_role_returns_405(self, make_server):
        server = await make_server()
        reader, writer = await server.open_connection()

        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 405 Method Not Allowed"
        writer.close()


class TestCompressionNegotiation:
    async def test_gzip_selected_when_only_gzip_acceptable(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("x" * 200)))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nAccept-Encoding: gzip\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert headers["content-encoding"] == "gzip"
        assert gzip.decompress(body) == b"x" * 200
        writer.close()

    async def test_br_preferred_over_gzip_when_both_acceptable(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("x" * 200)))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nAccept-Encoding: gzip, br\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert headers["content-encoding"] == "br"
        assert brotlicffi.decompress(body) == b"x" * 200
        writer.close()

    async def test_zstd_preferred_on_wildcard(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("x" * 200)))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nAccept-Encoding: *\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert headers["content-encoding"] == "zstd"
        assert zstandard.ZstdDecompressor().decompress(body) == b"x" * 200
        writer.close()

    async def test_deflate_selected_when_only_option(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("x" * 200)))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nAccept-Encoding: deflate\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert headers["content-encoding"] == "deflate"
        assert zlib.decompress(body) == b"x" * 200
        writer.close()

    async def test_no_accept_encoding_leaves_body_uncompressed(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("plain body")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert "content-encoding" not in headers
        assert body == b"plain body"
        writer.close()

    async def test_unacceptable_encodings_leave_body_uncompressed(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("plain body")))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nAccept-Encoding: identity;q=0, gzip;q=0, br;q=0, zstd;q=0, deflate;q=0\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert "content-encoding" not in headers
        assert body == b"plain body"
        writer.close()


class TestCompressionAndMinificationOrdering:
    async def test_minification_applied_before_compression_not_after(self, make_server):
        # Minification must run on the original text body, not on the
        # already-compressed bytes -- otherwise decompressing the response
        # yields garbage instead of minified HTML.
        original = "<html>   <body>hi</body>   </html>"
        expected = minify_html.minify(original, minify_js=True, minify_css=True, keep_comments=True, keep_html_and_head_opening_tags=True).encode("utf-8")

        server = await make_server(handler=handler_returning(
            lambda req: HTMLResponse(original, minification=True)
        ))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nAccept-Encoding: gzip\r\n\r\n")
        await writer.drain()

        _, headers, body = await read_http_response(reader)
        assert headers["content-encoding"] == "gzip"
        decompressed = gzip.decompress(body)
        assert decompressed == expected
        assert decompressed != original.encode()  # sanity: minification actually changed something
        writer.close()


class TestRangeRequests:
    async def test_range_request_returns_206_with_partial_content(self, make_server, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        async def on_request(request):
            range_header = request.headers.get("Range")
            rng = None
            if range_header and range_header.startswith("bytes="):
                start_s, _, end_s = range_header[6:].partition("-")
                rng = (int(start_s), int(end_s))
            return FileResponse(path, range=rng)

        server = await make_server(handler=Handler(on_request=on_request))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\nRange: bytes=2-5\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 206 Partial Content"
        assert headers["content-range"] == "bytes 2-5/10"
        assert body == b"2345"
        writer.close()

    async def test_full_file_without_range_header(self, make_server, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        server = await make_server(handler=handler_returning(lambda req: FileResponse(path)))
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer.drain()

        status_line, headers, body = await read_http_response(reader)
        assert status_line == "HTTP/1.1 200 OK"
        assert headers["accept-ranges"] == "bytes"
        assert body == b"0123456789"
        writer.close()


class TestLimits:
    async def test_max_connections_returns_503_and_closes(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")), max_connections=1)

        reader1, writer1 = await server.open_connection()
        # keep connection 1 open without sending a request so it still counts
        # as an active connection when connection 2 arrives.
        await asyncio.sleep(0.05)

        reader2, writer2 = await server.open_connection()
        data = await asyncio.wait_for(reader2.read(), timeout=5)
        assert b"503" in data
        assert b"Connection: close" in data

        writer1.close()
        writer2.close()

    async def test_rate_limiter_returns_429_after_burst_exhausted(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")), rate_limit=(0.0001, 1))

        reader1, writer1 = await server.open_connection()
        writer1.write(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer1.drain()
        status_line1, *_ = await read_http_response(reader1)
        assert status_line1 == "HTTP/1.1 200 OK"
        writer1.close()

        reader2, writer2 = await server.open_connection()
        writer2.write(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        await writer2.drain()
        status_line2, *_ = await read_http_response(reader2)
        assert status_line2 == "HTTP/1.1 429 Too Many Requests"
        writer2.close()

    async def test_idle_timeout_closes_connection_without_request(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")), idle_timeout=0.1)
        reader, writer = await server.open_connection()

        data = await asyncio.wait_for(reader.read(), timeout=5)
        assert data == b""
        writer.close()

    async def test_request_timeout_returns_408_for_incomplete_request(self, make_server):
        server = await make_server(handler=handler_returning(lambda req: PlainTextResponse("ok")), request_timeout=0.1)
        reader, writer = await server.open_connection()

        writer.write(b"GET / HTTP/1.1\r\n")  # incomplete: headers never terminated
        await writer.drain()

        status_line, *_ = await read_http_response(reader)
        assert status_line == "HTTP/1.1 408 Request Timeout"
        writer.close()
