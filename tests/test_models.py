import gzip

from momiji import Message, Request, Headers

def _sample_text() -> bytes:
    return ("The quick brown fox jumps over the lazy dog. " * 20).encode()

def test_compress_single_layer():
    original = _sample_text()
    msg = Message(headers=Headers({}), body=original, compression=True)
    msg.compress(["gzip"])

    assert msg.compressed is True
    assert msg.headers.get("Content-Encoding") == "gzip"
    assert gzip.decompress(msg.body) == original

def test_compress_decompress_round_trip():
    original = _sample_text()
    msg = Message(headers=Headers({}), body=original, compression=True)
    msg.compress(["gzip"])
    msg.decompress()

    assert msg.body == original
    assert msg.compressed is False
    assert "Content-Encoding" not in msg.headers

def test_compress_decompress_multi_layer():
    original = _sample_text()
    msg = Message(headers=Headers({}), body=original, compression=True)
    msg.compress(["gzip", "br"])
    msg.decompress()

    assert msg.body == original
    assert msg.compressed is False

def test_compress_noop_when_disabled():
    original = _sample_text()
    msg = Message(headers=Headers({}), body=original, compression=False)
    msg.compress(["gzip"])

    assert msg.body == original
    assert msg.compressed is False

def test_compress_noop_when_no_body():
    msg = Message(headers=Headers({}), body=None, compression=True)
    msg.compress(["gzip"])
    assert msg.compressed is False

def test_decompress_noop_when_not_compressed():
    original = _sample_text()
    msg = Message(headers=Headers({}), body=original, compression=True, compressed=False)
    msg.decompress()
    assert msg.body == original

def test_minify_html_shrinks_body():
    html = "<html>\n  <body>\n    <p>   hello   </p>\n  </body>\n</html>\n" * 5
    msg = Message(headers=Headers([("Content-Type", ["text/html"])]), body=html.encode(), minification=True)
    msg.minify()

    assert msg.minified is True
    assert len(msg.body) < len(html.encode())

def test_request_url_from_host_header():
    request = Request(
        method="GET",
        target="/x/y",
        headers=Headers([("Host", ["example.com:8080"])]),
    )
    assert request.url.host == "example.com"
    assert request.url.port == 8080
    assert request.url.path == "/x/y"

def test_has_real_body():
    assert Message(headers=Headers({}), body=b"x").has_real_body is True
    assert Message(headers=Headers({}), body=None).has_real_body is False

def test_json_property():
    msg = Message(headers=Headers({}), body=b'{"a": 1}')
    assert msg.json == {"a": 1}
