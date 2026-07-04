import re
import pytest

from momiji import Role, Request, Response, Headers, finalize_request, finalize_response

async def test_finalize_request_ok_with_host():
    request = Request(method="GET", target="/", headers=Headers([("Host", ["example.com"])]))
    await finalize_request(request, strict=True)

async def test_finalize_request_missing_host_strict_raises():
    request = Request(method="GET", target="/", headers=Headers({}))

    with pytest.raises(ValueError):
        await finalize_request(request, strict=True)

async def test_finalize_request_missing_host_lenient_ok():
    request = Request(method="GET", target="/", headers=Headers({}))
    await finalize_request(request, strict=False)

async def test_finalize_request_conflicting_framing():
    headers = Headers([
        ("Host", ["example.com"]),
        ("Content-Length", ["5"]),
        ("Transfer-Encoding", ["chunked"]),
    ])
    request = Request(method="POST", target="/", headers=headers)

    with pytest.raises(ValueError):
        await finalize_request(request, strict=True)

    request.headers.set("Content-Length", "5")
    await finalize_request(request, strict=False)
    assert "Content-Length" not in request.headers

async def test_finalize_response_content_length_for_bytes_body():
    response = Response(body=b"hello", headers=Headers({}), compression=False)
    await finalize_response(response)

    assert response.headers.get("Content-Length") == "5"
    assert re.match(r"^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$", response.headers.get("Date"))

async def test_finalize_response_no_content_length_for_204():
    response = Response(status_code=204, body=None, headers=Headers({}))
    await finalize_response(response)

    assert "Content-Length" not in response.headers

async def test_finalize_response_strips_hop_by_hop_for_proxy():
    headers = Headers([
        ("Connection", ["close, X-Custom"]),
        ("X-Custom", ["value"]),
        ("Keep-Alive", ["timeout=5"]),
    ])
    response = Response(body=b"hi", headers=headers)
    await finalize_response(response, role=Role.PROXY)

    assert "Connection" not in response.headers
    assert "X-Custom" not in response.headers
    assert "Keep-Alive" not in response.headers

async def test_finalize_response_range_over_file(tmp_path):
    path = tmp_path / "file.txt"
    content = b"0123456789" * 20
    path.write_bytes(content)

    response = Response(body=str(path), headers=Headers({}), range=(0, 99), compression=False)
    await finalize_response(response)

    assert response.status_code == 206
    assert response.headers.get("Content-Range") == f"bytes 0-99/{len(content)}"
    assert response.headers.get("Content-Length") == "100"
