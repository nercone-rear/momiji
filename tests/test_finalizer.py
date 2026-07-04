import pytest

from momiji.errors import HTTPViolationError
from momiji.finalizer import finalize_request, finalize_response, HOP_BY_HOP_HEADERS, FORBIDDEN_TRAILERS
from momiji.headers import Headers
from momiji.models import Role, Request, Response


def make_request(*, protocol="HTTP/1.1", headers=None, trailers=None, method="GET", target="/", body=None):
    return Request(
        method=method,
        target=target,
        protocol=protocol,
        headers=headers if headers is not None else Headers([]),
        trailers=trailers,
        body=body,
    )


class TestFinalizeRequestHost:
    async def test_http11_requires_host_when_strict(self):
        req = make_request(headers=Headers([]))
        with pytest.raises(HTTPViolationError):
            await finalize_request(req, strict=True)

    async def test_http11_host_not_required_when_not_strict(self):
        req = make_request(headers=Headers([]))
        await finalize_request(req, strict=False)  # must not raise

    async def test_http11_rejects_multiple_host_headers_even_when_not_strict(self):
        req = make_request(headers=Headers([("Host", ["a.com", "b.com"])]))
        with pytest.raises(HTTPViolationError):
            await finalize_request(req, strict=False)

    async def test_http11_single_host_header_ok(self):
        req = make_request(headers=Headers([("Host", ["a.com"])]))
        await finalize_request(req, strict=True)  # must not raise

    async def test_http10_does_not_require_host(self):
        req = make_request(protocol="HTTP/1.0", headers=Headers([]))
        await finalize_request(req, strict=True)  # must not raise


class TestFinalizeRequestBodyFraming:
    async def test_conflicting_transfer_encoding_and_content_length_strict_raises(self):
        req = make_request(headers=Headers([
            ("Host", ["a.com"]),
            ("Transfer-Encoding", ["chunked"]),
            ("Content-Length", ["5"]),
        ]))
        with pytest.raises(HTTPViolationError):
            await finalize_request(req, strict=True)

    async def test_conflicting_transfer_encoding_and_content_length_non_strict_drops_content_length(self):
        req = make_request(headers=Headers([
            ("Host", ["a.com"]),
            ("Transfer-Encoding", ["chunked"]),
            ("Content-Length", ["5"]),
        ]))
        await finalize_request(req, strict=False)
        assert "Content-Length" not in req.headers
        assert "Transfer-Encoding" in req.headers

    async def test_transfer_encoding_must_end_in_chunked(self):
        req = make_request(headers=Headers([
            ("Host", ["a.com"]),
            ("Transfer-Encoding", ["gzip"]),
        ]))
        with pytest.raises(HTTPViolationError):
            await finalize_request(req, strict=True)

    async def test_transfer_encoding_ending_in_chunked_is_accepted(self):
        req = make_request(headers=Headers([
            ("Host", ["a.com"]),
            ("Transfer-Encoding", ["gzip, chunked"]),
        ]))
        await finalize_request(req, strict=True)  # must not raise

    async def test_conflicting_content_length_values_raises(self):
        req = make_request(headers=Headers([
            ("Host", ["a.com"]),
            ("Content-Length", ["5", "6"]),
        ]))
        with pytest.raises(HTTPViolationError):
            await finalize_request(req, strict=True)

    async def test_repeated_identical_content_length_values_accepted(self):
        req = make_request(headers=Headers([
            ("Host", ["a.com"]),
            ("Content-Length", ["5", "5"]),
        ]))
        await finalize_request(req, strict=True)  # must not raise


class TestFinalizeRequestTrailers:
    async def test_forbidden_trailers_removed_when_strict(self):
        trailers = Headers([("Content-Length", ["5"]), ("X-Custom", ["1"])])
        req = make_request(headers=Headers([("Host", ["a.com"])]), trailers=trailers)
        await finalize_request(req, strict=True)
        assert "Content-Length" not in req.trailers
        assert "X-Custom" in req.trailers

    async def test_forbidden_trailers_kept_when_not_strict(self):
        trailers = Headers([("Content-Length", ["5"])])
        req = make_request(headers=Headers([]), trailers=trailers)
        await finalize_request(req, strict=False)
        assert "Content-Length" in req.trailers

    async def test_all_forbidden_trailer_names_are_stripped(self):
        trailers = Headers([(name.title(), ["x"]) for name in FORBIDDEN_TRAILERS])
        req = make_request(headers=Headers([("Host", ["a.com"])]), trailers=trailers)
        await finalize_request(req, strict=True)
        assert trailers.raw == []


def make_response(*, status_code=200, headers=None, body=None, protocol="HTTP/1.1", minification=False, range=None):
    resp = Response(status_code=status_code, headers=headers if headers is not None else Headers([]), body=body, protocol=protocol, minification=minification, range=range)
    return resp


class TestFinalizeResponseHopByHop:
    async def test_non_origin_role_strips_hop_by_hop_headers(self):
        headers = Headers([(name.title(), ["x"]) for name in HOP_BY_HOP_HEADERS])
        resp = make_response(headers=headers, body=b"hi")
        await finalize_response(resp, role=Role.PROXY)
        for name in HOP_BY_HOP_HEADERS:
            assert name not in resp.headers

    async def test_non_origin_role_strips_connection_named_headers(self):
        headers = Headers([("Connection", ["X-Custom"]), ("X-Custom", ["should-be-removed"])])
        resp = make_response(headers=headers, body=b"hi")
        await finalize_response(resp, role=Role.GATEWAY)
        assert "X-Custom" not in resp.headers

    async def test_origin_role_keeps_headers_untouched_pre_framing(self):
        headers = Headers([("X-Custom", ["keep-me"])])
        resp = make_response(headers=headers, body=b"hi")
        await finalize_response(resp, role=Role.ORIGIN)
        assert resp.headers.get("X-Custom") == "keep-me"


class TestFinalizeResponseStatusCodeFraming:
    @pytest.mark.parametrize("status_code", [100, 101, 199, 204, 304])
    async def test_bodyless_statuses_strip_length_and_transfer_encoding(self, status_code):
        resp = make_response(status_code=status_code, headers=Headers([("Content-Length", ["5"]), ("Transfer-Encoding", ["chunked"])]))
        await finalize_response(resp)
        assert "Content-Length" not in resp.headers
        assert "Transfer-Encoding" not in resp.headers


class TestFinalizeResponseBytesBody:
    async def test_sets_content_length_for_bytes_body(self):
        resp = make_response(body=b"hello")
        await finalize_response(resp)
        assert resp.headers.get("Content-Length") == "5"
        assert "Transfer-Encoding" not in resp.headers

    async def test_empty_bytes_body_has_zero_length(self):
        resp = make_response(body=b"")
        await finalize_response(resp)
        assert resp.headers.get("Content-Length") == "0"


class TestFinalizeResponseNoneBody:
    async def test_none_body_sets_zero_content_length(self):
        resp = make_response(body=None)
        await finalize_response(resp)
        assert resp.headers.get("Content-Length") == "0"
        assert "Transfer-Encoding" not in resp.headers


class TestFinalizeResponseStreamingBody:
    async def test_http11_uses_chunked_transfer_encoding(self):
        async def gen():
            yield b"a"

        resp = make_response(body=gen(), protocol="HTTP/1.1")
        await finalize_response(resp)
        assert resp.headers.get("Transfer-Encoding") == "chunked"
        assert "Content-Length" not in resp.headers

    async def test_http10_closes_connection_instead_of_chunking(self):
        async def gen():
            yield b"a"

        resp = make_response(body=gen(), protocol="HTTP/1.0")
        await finalize_response(resp)
        assert resp.headers.get("Connection") == "close"
        assert "Content-Length" not in resp.headers
        assert "Transfer-Encoding" not in resp.headers


class TestFinalizeResponseFileBody:
    async def test_small_file_with_minification_is_inlined(self, tmp_path):
        path = tmp_path / "index.html"
        path.write_text("<html>   <body>hi</body>   </html>")

        resp = make_response(body=str(path), minification=True, headers=Headers([("Content-Type", ["text/html"])]))
        await finalize_response(resp)

        assert isinstance(resp.body, bytes)
        assert resp.headers.get("Content-Length") == str(len(resp.body))
        assert "Transfer-Encoding" not in resp.headers

    async def test_file_without_minification_streams_with_accept_ranges(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        resp = make_response(body=str(path))
        await finalize_response(resp)

        assert resp.body == str(path)
        assert resp.headers.get("Accept-Ranges") == "bytes"
        assert resp.headers.get("Content-Length") == "10"

    async def test_valid_range_returns_206_with_content_range(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        resp = make_response(body=str(path), range=(2, 5))
        await finalize_response(resp)

        assert resp.status_code == 206
        assert resp.headers.get("Content-Range") == "bytes 2-5/10"
        assert resp.headers.get("Content-Length") == "4"

    async def test_range_end_beyond_file_size_is_clamped(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        resp = make_response(body=str(path), range=(5, 1000))
        await finalize_response(resp)

        assert resp.status_code == 206
        assert resp.headers.get("Content-Range") == "bytes 5-9/10"
        assert resp.headers.get("Content-Length") == "5"

    async def test_range_start_beyond_file_size_returns_416(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        resp = make_response(body=str(path), range=(20, 30))
        await finalize_response(resp)

        assert resp.status_code == 416
        assert resp.body is None
        assert resp.headers.get("Content-Range") == "bytes */10"
        assert resp.headers.get("Content-Length") == "0"

    async def test_range_start_greater_than_end_returns_416(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        resp = make_response(body=str(path), range=(5, 2))
        await finalize_response(resp)

        assert resp.status_code == 416

    async def test_negative_range_start_returns_416(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"0123456789")

        resp = make_response(body=str(path), range=(-1, 5))
        await finalize_response(resp)

        assert resp.status_code == 416

    async def test_pathlike_body_supported(self, tmp_path):
        path = tmp_path / "data.bin"
        path.write_bytes(b"hello")

        resp = make_response(body=path)
        await finalize_response(resp)

        assert resp.headers.get("Content-Length") == "5"


class TestFinalizeResponseDateAndServerHeaders:
    async def test_date_header_always_set(self):
        resp = make_response(body=b"hi")
        await finalize_response(resp)
        assert "Date" in resp.headers

    async def test_server_header_set_when_absent(self):
        resp = make_response(body=b"hi")
        await finalize_response(resp)
        assert resp.headers.get("Server") == "Momiji"

    async def test_server_header_not_overridden_when_present(self):
        resp = make_response(body=b"hi", headers=Headers([("Server", ["CustomServer"])]))
        await finalize_response(resp)
        assert resp.headers.get("Server") == "CustomServer"
