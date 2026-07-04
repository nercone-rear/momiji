import gzip
import zlib

import brotlicffi
import zstandard

from momiji.models import Message, Request, Response
from momiji.headers import Headers


class TestMessageText:
    def test_text_decodes_body(self):
        m = Message(body=b"hello")
        assert m.text == "hello"

    def test_json_parses_body(self):
        m = Message(body=b'{"a": 1}')
        assert m.json == {"a": 1}


class TestHasRealBody:
    def test_true_for_bytes_body(self):
        assert Message(body=b"x").has_real_body

    def test_false_for_none_body(self):
        assert not Message(body=None).has_real_body

    def test_false_for_path_body(self):
        assert not Message(body="/tmp/foo").has_real_body


class TestCompress:
    def test_gzip_round_trip(self):
        m = Message(body=b"hello world", headers=Headers([]))
        m.compress(["gzip"])
        assert m.compressed
        assert gzip.decompress(m.body) == b"hello world"
        assert m.headers.get("Content-Encoding") == "gzip"

    def test_deflate_round_trip(self):
        m = Message(body=b"hello world", headers=Headers([]))
        m.compress(["deflate"])
        assert zlib.decompress(m.body) == b"hello world"

    def test_brotli_round_trip(self):
        m = Message(body=b"hello world", headers=Headers([]))
        m.compress(["br"])
        assert brotlicffi.decompress(m.body) == b"hello world"

    def test_zstd_round_trip(self):
        m = Message(body=b"hello world", headers=Headers([]))
        m.compress(["zstd"])
        assert zstandard.ZstdDecompressor().decompress(m.body) == b"hello world"

    def test_noop_when_compression_disabled(self):
        m = Message(body=b"hello", headers=Headers([]), compression=False)
        m.compress(["gzip"])
        assert m.body == b"hello"
        assert not m.compressed

    def test_noop_when_already_compressed(self):
        m = Message(body=b"already", headers=Headers([]), compressed=True)
        m.compress(["gzip"])
        assert m.body == b"already"

    def test_noop_when_body_is_none(self):
        m = Message(body=None, headers=Headers([]))
        m.compress(["gzip"])
        assert m.body is None
        assert not m.compressed

    def test_unknown_encoding_entirely_skipped(self):
        m = Message(body=b"hello", headers=Headers([]))
        m.compress(["bogus"])
        assert m.body == b"hello"
        assert not m.compressed
        assert m.headers.get("Content-Encoding") == ""

    def test_non_bytes_body_still_sets_content_encoding(self):
        m = Message(body="/some/path", headers=Headers([]))
        m.compress(["gzip"])
        assert m.body == "/some/path"
        assert m.headers.get("Content-Encoding") == "gzip"
        assert m.compressed

    def test_multiple_encodings_applied_in_order(self):
        m = Message(body=b"hello world", headers=Headers([]))
        m.compress(["gzip", "br"])
        # br was applied last, so decoding requires reversing: br then gzip
        assert gzip.decompress(brotlicffi.decompress(m.body)) == b"hello world"
        assert m.headers.get("Content-Encoding") == "gzip, br"


class TestDecompress:
    def test_gzip_round_trip(self):
        original = b"hello world"
        m = Message(body=gzip.compress(original), headers=Headers([("Content-Encoding", ["gzip"])]), compressed=True)
        m.decompress()
        assert m.body == original
        assert not m.compressed
        assert "Content-Encoding" not in m.headers

    def test_deflate_zlib_wrapped(self):
        original = b"hello world"
        m = Message(body=zlib.compress(original), headers=Headers([("Content-Encoding", ["deflate"])]), compressed=True)
        m.decompress()
        assert m.body == original

    def test_deflate_raw(self):
        original = b"hello world"
        compressor = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
        raw = compressor.compress(original) + compressor.flush()
        m = Message(body=raw, headers=Headers([("Content-Encoding", ["deflate"])]), compressed=True)
        m.decompress()
        assert m.body == original

    def test_brotli_round_trip(self):
        original = b"hello world"
        m = Message(body=brotlicffi.compress(original), headers=Headers([("Content-Encoding", ["br"])]), compressed=True)
        m.decompress()
        assert m.body == original

    def test_zstd_round_trip(self):
        original = b"hello world"
        compressed = zstandard.ZstdCompressor().compress(original)
        m = Message(body=compressed, headers=Headers([("Content-Encoding", ["zstd"])]), compressed=True)
        m.decompress()
        assert m.body == original

    def test_multiple_encodings_reversed_on_decode(self):
        original = b"hello world"
        step1 = gzip.compress(original)
        step2 = brotlicffi.compress(step1)
        m = Message(body=step2, headers=Headers([("Content-Encoding", ["gzip", "br"])]), compressed=True)
        m.decompress()
        assert m.body == original

    def test_noop_when_not_marked_compressed(self):
        m = Message(body=b"raw", headers=Headers([]), compressed=False)
        m.decompress()
        assert m.body == b"raw"


class TestMinify:
    def test_minifies_html(self):
        m = Message(body=b"<html>   <body>  hi  </body>  </html>", headers=Headers([("Content-Type", ["text/html"])]), minification=True)
        m.minify()
        assert m.minified
        assert len(m.body) <= len(b"<html>   <body>  hi  </body>  </html>")

    def test_minifies_css(self):
        m = Message(body=b"body {  color: red;  }", headers=Headers([("Content-Type", ["text/css"])]), minification=True)
        m.minify()
        assert m.minified
        assert b" " not in m.body or len(m.body) < len(b"body {  color: red;  }")

    def test_minifies_javascript(self):
        m = Message(body=b"function foo() {  return 1;  }", headers=Headers([("Content-Type", ["application/javascript"])]), minification=True)
        m.minify()
        assert m.minified

    def test_minifies_svg(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><!-- comment --><rect x="0" y="0"/></svg>'
        m = Message(body=svg, headers=Headers([("Content-Type", ["image/svg+xml"])]), minification=True)
        m.minify()
        assert m.minified
        assert b"comment" not in m.body

    def test_noop_when_minification_disabled(self):
        m = Message(body=b"<html>  </html>", headers=Headers([("Content-Type", ["text/html"])]), minification=False)
        m.minify()
        assert not m.minified
        assert m.body == b"<html>  </html>"

    def test_noop_for_unrecognized_content_type(self):
        original = b"just some bytes"
        m = Message(body=original, headers=Headers([("Content-Type", ["application/octet-stream"])]), minification=True)
        m.minify()
        assert not m.minified
        assert m.body == original

    def test_noop_when_already_minified(self):
        m = Message(body=b"<html></html>", headers=Headers([("Content-Type", ["text/html"])]), minification=True, minified=True)
        m.minify()
        assert m.body == b"<html></html>"

    def test_noop_when_body_is_not_bytes(self):
        m = Message(body="/some/path", headers=Headers([("Content-Type", ["text/html"])]), minification=True)
        m.minify()
        assert not m.minified

    def test_invalid_markup_does_not_raise(self):
        m = Message(body=b"<<<not valid>>>", headers=Headers([("Content-Type", ["image/svg+xml"])]), minification=True)
        m.minify()  # must not raise regardless of outcome


class TestRequest:
    def test_url_derived_from_target_and_host_header(self):
        req = Request(method="GET", target="/foo", headers=Headers([("Host", ["example.com"])]))
        assert req.url.host == "example.com"
        assert req.url.path == "/foo"

    def test_url_with_no_host_header(self):
        req = Request(method="GET", target="/foo", headers=Headers([]))
        assert req.url.host == ""


class TestIsWebsocketUpgrade:
    def _req(self, *, upgrade="websocket", connection="Upgrade", method="GET"):
        return Request(method=method, target="/ws", headers=Headers([("Upgrade", [upgrade]), ("Connection", [connection])]))

    def test_true_for_proper_upgrade_request(self):
        assert self._req().is_websocket_upgrade

    def test_true_when_upgrade_is_one_of_several_connection_tokens(self):
        assert self._req(connection="keep-alive, Upgrade").is_websocket_upgrade

    def test_false_when_connection_token_only_contains_upgrade_as_substring(self):
        # A bogus token like "some-upgrade-thing" must not satisfy the
        # Connection: Upgrade requirement via a loose substring match.
        assert not self._req(connection="some-upgrade-thing").is_websocket_upgrade

    def test_false_for_non_get_method(self):
        assert not self._req(method="POST").is_websocket_upgrade

    def test_false_when_upgrade_header_is_not_websocket(self):
        assert not self._req(upgrade="h2c").is_websocket_upgrade


class TestResponse:
    def test_defaults(self):
        resp = Response()
        assert resp.status_code == 200
        assert resp.range is None

    def test_custom_status_code(self):
        resp = Response(status_code=404)
        assert resp.status_code == 404
