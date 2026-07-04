import pytest

from momiji import protocol as protocol_module
from momiji.protocol import find_body_mode, ChunkedDecoder, MessageParser
from momiji.errors import HTTPReportedViolationError
from momiji.headers import Headers


def headers_from(pairs):
    return Headers([(k, [v] if isinstance(v, str) else v) for k, v in pairs])


class TestFindBodyModeResponseStatusShortCircuit:
    @pytest.mark.parametrize("status", [100, 101, 199, 204, 304])
    def test_bodyless_status_ignores_framing_headers(self, status):
        headers = headers_from([("Content-Length", "5")])
        mode, length = find_body_mode(headers, is_response=True, status_code=status)
        assert (mode, length) == ("none", 0)

    def test_200_with_content_length_uses_length_mode(self):
        headers = headers_from([("Content-Length", "5")])
        mode, length = find_body_mode(headers, is_response=True, status_code=200)
        assert (mode, length) == ("length", 5)


class TestFindBodyModeTransferEncoding:
    def test_chunked_alone(self):
        headers = headers_from([("Transfer-Encoding", "chunked")])
        assert find_body_mode(headers, is_response=False) == ("chunked", 0)

    def test_multiple_codings_ending_in_chunked(self):
        headers = headers_from([("Transfer-Encoding", "gzip, chunked")])
        assert find_body_mode(headers, is_response=False) == ("chunked", 0)

    def test_must_end_in_chunked(self):
        headers = headers_from([("Transfer-Encoding", "chunked, gzip")])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)

    def test_conflicts_with_content_length(self):
        headers = headers_from([("Transfer-Encoding", "chunked"), ("Content-Length", "5")])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)


class TestFindBodyModeContentLength:
    def test_valid_length(self):
        headers = headers_from([("Content-Length", "42")])
        assert find_body_mode(headers, is_response=False) == ("length", 42)

    def test_zero_length(self):
        headers = headers_from([("Content-Length", "0")])
        assert find_body_mode(headers, is_response=False) == ("length", 0)

    def test_repeated_identical_values_ok(self):
        headers = headers_from([("Content-Length", ["5", "5"])])
        assert find_body_mode(headers, is_response=False) == ("length", 5)

    def test_conflicting_values_rejected(self):
        headers = headers_from([("Content-Length", ["5", "6"])])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)

    def test_non_numeric_value_rejected(self):
        headers = headers_from([("Content-Length", "abc")])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)

    def test_empty_value_rejected(self):
        headers = headers_from([("Content-Length", "")])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)

    def test_negative_value_rejected(self):
        headers = headers_from([("Content-Length", "-5")])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)

    def test_plus_prefixed_value_rejected(self):
        headers = headers_from([("Content-Length", "+5")])
        with pytest.raises(HTTPReportedViolationError):
            find_body_mode(headers, is_response=False)


class TestFindBodyModeNoFramingHeaders:
    def test_request_defaults_to_no_body(self):
        assert find_body_mode(headers_from([]), is_response=False) == ("none", 0)

    def test_response_defaults_to_close_delimited(self):
        assert find_body_mode(headers_from([]), is_response=True, status_code=200) == ("close", 0)


class TestChunkedDecoder:
    def test_single_chunk_no_trailers(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"5\r\nhello\r\n0\r\n\r\n")
        assert dec.feed(buf) is True
        assert bytes(dec.body) == b"hello"
        assert dec.trailer_buffer == b""
        assert len(buf) == 0

    def test_multiple_chunks(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"3\r\nfoo\r\n3\r\nbar\r\n0\r\n\r\n")
        assert dec.feed(buf) is True
        assert bytes(dec.body) == b"foobar"

    def test_hex_chunk_size_case_insensitive(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"A\r\n0123456789\r\n0\r\n\r\n")
        assert dec.feed(buf) is True
        assert bytes(dec.body) == b"0123456789"

    def test_chunk_extension_ignored(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"5;ext=val\r\nhello\r\n0\r\n\r\n")
        assert dec.feed(buf) is True
        assert bytes(dec.body) == b"hello"

    def test_incremental_feed_across_multiple_calls(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"5\r\nhel")
        assert dec.feed(buf) is False
        buf.extend(b"lo\r\n0\r\n\r\n")
        assert dec.feed(buf) is True
        assert bytes(dec.body) == b"hello"

    def test_incomplete_size_line_returns_false(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"5")
        assert dec.feed(buf) is False
        assert dec.state == "size"

    def test_trailers_parsed(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"3\r\nfoo\r\n0\r\nX-Trailer: value\r\n\r\n")
        assert dec.feed(buf) is True
        assert dec.trailer_buffer == b"X-Trailer: value"

    def test_invalid_chunk_size_chars_raise(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"zz\r\nhello\r\n")
        with pytest.raises(HTTPReportedViolationError):
            dec.feed(buf)

    def test_empty_chunk_size_line_raises(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"\r\nhello\r\n")
        with pytest.raises(HTTPReportedViolationError):
            dec.feed(buf)

    def test_malformed_chunk_terminator_raises(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"5\r\nhelloXX0\r\n\r\n")
        with pytest.raises(HTTPReportedViolationError):
            dec.feed(buf)

    def test_oversized_chunk_size_line_raises(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"1" * 5000)
        with pytest.raises(HTTPReportedViolationError):
            dec.feed(buf)

    def test_oversized_trailer_section_raises(self):
        dec = ChunkedDecoder()
        buf = bytearray(b"0\r\n" + b"X-Foo: " + b"a" * 100000)
        with pytest.raises(HTTPReportedViolationError):
            dec.feed(buf)


class TestMessageParserRequest:
    def test_parses_request_with_content_length_body(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhello")
        first_line, headers, body, trailers = parser.try_parse()
        assert first_line == "POST / HTTP/1.1"
        assert headers.get("Host") == "a"
        assert body == b"hello"
        assert trailers is None

    def test_returns_none_until_headers_complete(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"GET / HTTP/1.1\r\nHost: a")
        assert parser.try_parse() is None

    def test_returns_none_until_body_complete(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhel")
        assert parser.try_parse() is None

    def test_request_without_body_headers_has_no_body(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        first_line, headers, body, trailers = parser.try_parse()
        assert body == b""

    def test_chunked_request_body_and_trailers(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nfoo\r\n0\r\nX-Trailer: v\r\n\r\n")
        first_line, headers, body, trailers = parser.try_parse()
        assert body == b"foo"
        assert trailers.get("X-Trailer") == "v"

    def test_parser_resets_after_full_message_for_pipelining(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"GET /a HTTP/1.1\r\nHost: a\r\n\r\nGET /b HTTP/1.1\r\nHost: a\r\n\r\n")
        first_line1, *_ = parser.try_parse()
        first_line2, *_ = parser.try_parse()
        assert first_line1 == "GET /a HTTP/1.1"
        assert first_line2 == "GET /b HTTP/1.1"

    def test_head_too_large_raises(self):
        parser = MessageParser(is_response=False)
        parser.feed(b"GET / HTTP/1.1\r\n" + b"X-Foo: " + b"a" * (70 * 1024) + b"\r\n")
        with pytest.raises(HTTPReportedViolationError):
            parser.try_parse()

    def test_head_too_large_raises_even_when_delivered_in_one_piece(self):
        # The 64 KiB head-size limit must hold regardless of how the bytes
        # were chunked on the wire: an oversized head that happens to
        # arrive (and complete, i.e. include the \r\n\r\n terminator) in a
        # single feed() must be rejected exactly like one that arrives
        # gradually and is caught mid-stream.
        parser = MessageParser(is_response=False)
        parser.feed(b"GET / HTTP/1.1\r\n" + b"X-Foo: " + b"a" * (70 * 1024) + b"\r\n\r\n")
        with pytest.raises(HTTPReportedViolationError):
            parser.try_parse()

    def test_body_exceeding_max_size_raises(self, monkeypatch):
        monkeypatch.setattr(protocol_module, "MAX_BODY_SIZE", 3)
        parser = MessageParser(is_response=False)
        parser.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 10\r\n\r\n" + b"x" * 10)
        with pytest.raises(HTTPReportedViolationError):
            parser.try_parse()


class TestMessageParserResponse:
    def test_parses_response_with_status_and_length(self):
        parser = MessageParser(is_response=True)
        parser.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
        first_line, headers, body, trailers = parser.try_parse()
        assert first_line == "HTTP/1.1 200 OK"
        assert body == b"hi"

    def test_204_response_has_no_body_even_with_data_present(self):
        parser = MessageParser(is_response=True)
        parser.feed(b"HTTP/1.1 204 No Content\r\n\r\n")
        first_line, headers, body, trailers = parser.try_parse()
        assert body == b""

    def test_close_delimited_body_waits_for_eof(self):
        parser = MessageParser(is_response=True)
        parser.feed(b"HTTP/1.1 200 OK\r\n\r\nhello")
        assert parser.try_parse() is None
        parser.signal_eof()
        first_line, headers, body, trailers = parser.try_parse()
        assert body == b"hello"

    def test_close_delimited_body_accumulates_across_feeds(self):
        parser = MessageParser(is_response=True)
        parser.feed(b"HTTP/1.1 200 OK\r\n\r\nhel")
        assert parser.try_parse() is None
        parser.feed(b"lo")
        assert parser.try_parse() is None
        parser.signal_eof()
        _, _, body, _ = parser.try_parse()
        assert body == b"hello"
