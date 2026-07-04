import pytest

from momiji.headers import Headers, CommaHeader, Link, AcceptEncoding, ContentType, ETag
from momiji.errors import HTTPViolationError

def test_headers_parse_build_round_trip():
    headers = Headers.parse("Content-Type: text/plain\r\nContent-Length: 5\r\n")
    assert headers.build() == "Content-Type: text/plain\r\nContent-Length: 5\r\n"

def test_headers_repeated():
    headers = Headers.parse("Set-Cookie: a=1\r\nSet-Cookie: b=2\r\n")
    assert headers["Set-Cookie"] == ["a=1", "b=2"]
    assert headers.get("Set-Cookie") == "a=1, b=2"

def test_headers_case_insensitive():
    headers = Headers.parse("Content-Type: text/plain\r\n")
    assert headers.get("content-type") == "text/plain"
    assert "CONTENT-TYPE" in headers

def test_headers_set_override():
    headers = Headers([])
    headers.set("X-Foo", "1")
    headers.set("X-Foo", "2", override=False)
    assert headers.get("X-Foo") == "1"
    headers.set("X-Foo", "3", override=True)
    assert headers.get("X-Foo") == "3"

def test_headers_append():
    headers = Headers([])
    headers.append("X-Foo", "1")
    headers.append("X-Foo", "2")
    assert headers["X-Foo"] == ["1", "2"]

def test_headers_remove_and_contains():
    headers = Headers.parse("X-Foo: 1\r\n")
    assert "X-Foo" in headers
    headers.remove("X-Foo")
    assert "X-Foo" not in headers

def test_headers_obs_fold_rejected():
    with pytest.raises(HTTPViolationError):
        Headers.parse("X-Foo: 1\r\n bar\r\n")

def test_headers_malformed_line():
    with pytest.raises(HTTPViolationError):
        Headers.parse("this has no colon\r\n")

def test_headers_invalid_name():
    with pytest.raises(HTTPViolationError):
        Headers.parse("X Foo: 1\r\n")

def test_comma_header_round_trip():
    header = CommaHeader("gzip, br")
    assert header.raw == ["gzip", "br"]
    header.append("deflate")
    assert "deflate" in header
    header.remove("gzip")
    assert header.build() == "br, deflate"

def test_link_parse_quoted_comma():
    link = Link.parse('<https://a>; rel="next", <https://b>; rel="prev,x"')
    assert len(link.raw) == 2
    assert link.raw[0][0] == "https://a"
    assert link.raw[0][1]["rel"] == "next"
    assert link.raw[1][1]["rel"] == "prev,x"

def test_link_build_round_trip():
    link = Link([("https://a", {"rel": "next"})])
    text = link.build()
    parsed = Link.parse(text)
    assert parsed.raw == [("https://a", {"rel": "next"})]

def test_accept_encoding_parse():
    accept = AcceptEncoding.parse("gzip;q=0.8, br, deflate;q=0.5")
    assert accept.raw == [("gzip", 0.8), ("br", 1.0), ("deflate", 0.5)]

def test_accept_encoding_build():
    accept = AcceptEncoding([("br", 1.0), ("gzip", 0.5)])
    assert accept.build() == "br, gzip;q=0.5"

def test_content_type_essence_and_charset():
    ct = ContentType("text/html; charset=UTF-8")
    assert ct.essence == "text/html"
    assert ct.charset == "utf-8"

def test_content_type_boundary():
    ct = ContentType('multipart/form-data; boundary="----abc"')
    assert ct.boundary == "----abc"

def test_content_type_build_round_trip():
    ct = ContentType("text/html; charset=utf-8")
    rebuilt = ContentType(ct.build())
    assert rebuilt.essence == "text/html"
    assert rebuilt.charset == "utf-8"

def test_etag_strong_and_weak_match():
    assert ETag('"abc"').strong_match('"abc"') is True
    assert ETag('W/"abc"').strong_match('"abc"') is False
    assert ETag('W/"abc"').weak_match('"abc"') is True
    assert ETag('"abc"').strong_match('"xyz"') is False

def test_etag_match_strong_weak_flags():
    tag = ETag('W/"abc"')
    assert tag.match('"abc"', strong=True, weak=False) is False
    assert tag.match('"abc"', strong=False, weak=True) is True
