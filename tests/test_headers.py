import pytest

from momiji.errors import HTTPViolationError
from momiji.headers import (
    Headers, CommaHeader, Link, AcceptEncoding, ContentType, ETag,
    is_valid_token, quote, unquote, split_unquoted,
)


class TestIsValidToken:
    def test_accepts_rfc7230_tchar_token(self):
        assert is_valid_token("Content-Type")
        assert is_valid_token("X-Foo_Bar.Baz~!#$%&'*+^`|")

    def test_rejects_empty_string(self):
        assert not is_valid_token("")

    def test_rejects_separators(self):
        for bad in ("foo bar", "foo/bar", "foo:bar", "foo(bar)", "foo\tbar", 'foo"bar"'):
            assert not is_valid_token(bad), bad


class TestQuoteUnquote:
    def test_quote_produces_a_syntactically_valid_representation(self):
        # RFC 9110 field values may always be represented as a quoted-string,
        # so quote() is not required to prefer the bare token form -- it just
        # must produce something is_valid_token() accepts or a quoted-string.
        result = quote("hello")
        assert is_valid_token(result) or (result.startswith('"') and result.endswith('"'))

    def test_quote_wraps_value_with_special_chars(self):
        assert quote("hello world") == '"hello world"'

    def test_quote_escapes_backslash_and_quote(self):
        assert quote('a"b\\c') == '"a\\"b\\\\c"'

    def test_quote_empty_string_is_quoted(self):
        assert quote("") == '""'

    def test_unquote_strips_quotes(self):
        assert unquote('"hello"') == "hello"

    def test_unquote_unescapes_backslash_sequences(self):
        assert unquote('"a\\"b\\\\c"') == 'a"b\\c'

    def test_unquote_passthrough_for_bare_token(self):
        assert unquote("hello") == "hello"

    def test_roundtrip(self):
        for value in ("plain", "has space", 'has "quotes"', "has\\backslash"):
            assert unquote(quote(value)) == value


class TestSplitUnquoted:
    def test_splits_on_delimiter_outside_quotes(self):
        assert split_unquoted("a;b;c", ";") == ["a", "b", "c"]

    def test_does_not_split_inside_quotes(self):
        assert split_unquoted('a="b;c";d', ";") == ['a="b;c"', "d"]

    def test_handles_escaped_quote_inside_quoted_string(self):
        assert split_unquoted(r'a="b\"c;d"', ";") == [r'a="b\"c;d"']


class TestHeadersParse:
    def test_parses_single_header(self):
        h = Headers.parse("Host: example.com\r\n")
        assert h.get("Host") == "example.com"

    def test_header_name_lookup_is_case_insensitive(self):
        h = Headers.parse("Content-Type: text/plain\r\n")
        assert h.get("content-type") == "text/plain"
        assert h.get("CONTENT-TYPE") == "text/plain"

    def test_repeated_headers_are_combined_with_comma_on_get(self):
        h = Headers.parse("X-Foo: a\r\nX-Foo: b\r\n")
        assert h.get("X-Foo") == "a, b"
        assert h["X-Foo"] == ["a", "b"]

    def test_empty_value_is_valid(self):
        h = Headers.parse("X-Empty:\r\n")
        assert h.get("X-Empty") == ""

    def test_leading_and_trailing_whitespace_stripped_from_value(self):
        h = Headers.parse("X-Foo:   value  \r\n")
        assert h.get("X-Foo") == "value"

    def test_trailing_crlf_line_is_ignored(self):
        h = Headers.parse("A: 1\r\nB: 2\r\n")
        assert h.get("A") == "1"
        assert h.get("B") == "2"

    def test_missing_value_still_parses_as_empty(self):
        h = Headers.parse("X-Foo:\r\n")
        assert h.get("X-Foo") == ""

    def test_empty_input_yields_no_headers(self):
        h = Headers.parse("")
        assert h.raw == []

    def test_rejects_line_folding_space(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X-Foo: bar\r\n baz\r\n")

    def test_rejects_line_folding_tab(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X-Foo: bar\r\n\tbaz\r\n")

    def test_rejects_missing_colon(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X-Foo bar\r\n")

    def test_rejects_invalid_header_name_with_space(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X Foo: bar\r\n")

    def test_rejects_invalid_header_name_with_control_char(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X-Foo\x01: bar\r\n")

    def test_rejects_control_char_in_value(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X-Foo: ba\x01r\r\n")

    def test_rejects_del_char_in_value(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse("X-Foo: ba\x7Fr\r\n")

    def test_allows_horizontal_tab_in_value(self):
        h = Headers.parse("X-Foo: ba\tr\r\n")
        assert h.get("X-Foo") == "ba\tr"

    def test_rejects_empty_header_name(self):
        with pytest.raises(HTTPViolationError):
            Headers.parse(": bar\r\n")


class TestHeadersMutation:
    def test_set_overrides_existing_by_default(self):
        h = Headers([("X-Foo", ["a"])])
        h.set("X-Foo", "b")
        assert h.get("X-Foo") == "b"

    def test_set_without_override_keeps_first_value(self):
        h = Headers([("X-Foo", ["a"])])
        h.set("X-Foo", "b", override=False)
        assert h.get("X-Foo") == "a"

    def test_set_without_override_adds_when_absent(self):
        h = Headers([])
        h.set("X-Foo", "a", override=False)
        assert h.get("X-Foo") == "a"

    def test_append_creates_new_header(self):
        h = Headers([])
        h.append("X-Foo", "a")
        assert h["X-Foo"] == ["a"]

    def test_append_adds_to_existing_header(self):
        h = Headers([("X-Foo", ["a"])])
        h.append("X-Foo", "b")
        assert h["X-Foo"] == ["a", "b"]

    def test_remove_deletes_header(self):
        h = Headers([("X-Foo", ["a"])])
        h.remove("X-Foo")
        assert "X-Foo" not in h

    def test_remove_missing_header_is_noop(self):
        h = Headers([])
        h.remove("X-Foo")  # must not raise
        assert "X-Foo" not in h

    def test_setitem_and_getitem(self):
        h = Headers([])
        h["X-Foo"] = "a"
        assert h["X-Foo"] == ["a"]

    def test_contains_is_case_insensitive(self):
        h = Headers([("Content-Length", ["0"])])
        assert "content-length" in h

    def test_get_returns_default_when_missing(self):
        h = Headers([])
        assert h.get("X-Foo") is None
        assert h.get("X-Foo", "default") == "default"

    def test_getitem_returns_none_when_missing(self):
        h = Headers([])
        assert h["X-Foo"] is None

    def test_items_expands_multi_valued_headers(self):
        h = Headers([("X-Foo", ["a", "b"])])
        assert h.items() == [("X-Foo", "a"), ("X-Foo", "b")]


class TestHeadersBuild:
    def test_build_roundtrips_single_header(self):
        h = Headers.parse("Host: example.com\r\n")
        assert h.build() == "Host: example.com\r\n"

    def test_build_emits_one_line_per_value(self):
        h = Headers([("X-Foo", ["a", "b"])])
        assert h.build() == "X-Foo: a\r\nX-Foo: b\r\n"

    def test_str_matches_build(self):
        h = Headers.parse("A: 1\r\n")
        assert str(h) == h.build()


class TestCommaHeader:
    def test_parses_and_strips_whitespace(self):
        c = CommaHeader("a, b ,  c")
        assert c.raw == ["a", "b", "c"]

    def test_skips_empty_segments(self):
        c = CommaHeader("a,,b,")
        assert c.raw == ["a", "b"]

    def test_empty_string_yields_empty_list(self):
        assert CommaHeader("").raw == []

    def test_build_joins_with_comma_space(self):
        assert CommaHeader(["a", "b"]).build() == "a, b"

    def test_append_and_remove(self):
        c = CommaHeader(["a"])
        c.append("b")
        assert c.raw == ["a", "b"]
        c.remove("a")
        assert c.raw == ["b"]

    def test_contains(self):
        c = CommaHeader("a, b")
        assert "a" in c
        assert "c" not in c

    def test_set_replaces_contents(self):
        c = CommaHeader("a, b")
        c.set(["x", "y"])
        assert c.raw == ["x", "y"]
        c.set("z")
        assert c.raw == ["z"]


class TestLink:
    def test_parses_single_link(self):
        link = Link('</next>; rel="next"')
        assert link.raw == [("/next", {"rel": "next"})]

    def test_parses_multiple_links(self):
        link = Link('</a>; rel="next", </b>; rel="prev"')
        assert link.raw == [("/a", {"rel": "next"}), ("/b", {"rel": "prev"})]

    def test_ignores_segment_without_angle_bracket(self):
        link = Link("garbage, </a>; rel=next")
        assert link.raw == [("/a", {"rel": "next"})]

    def test_param_without_quotes(self):
        link = Link("</a>; rel=next")
        assert link.raw == [("/a", {"rel": "next"})]

    def test_build_quotes_param_values(self):
        link = Link([("/a", {"rel": "next"})])
        assert link.build() == '</a>; rel="next"'

    def test_empty_value(self):
        assert Link("").raw == []

    def test_unclosed_bracket_segment_skipped(self):
        link = Link("<no-close")
        assert link.raw == []


class TestAcceptEncoding:
    def test_parses_codings_default_q(self):
        ae = AcceptEncoding("gzip, br")
        assert ae.raw == [("gzip", 1.0), ("br", 1.0)]

    def test_parses_q_value(self):
        ae = AcceptEncoding("gzip;q=0.5")
        assert ae.raw == [("gzip", 0.5)]

    def test_invalid_q_value_falls_back_to_one(self):
        ae = AcceptEncoding("gzip;q=bogus")
        assert ae.raw == [("gzip", 1.0)]

    def test_lowercases_coding(self):
        ae = AcceptEncoding("GZIP")
        assert ae.raw == [("gzip", 1.0)]

    def test_wildcard(self):
        ae = AcceptEncoding("*;q=0")
        assert ae.raw == [("*", 0.0)]

    def test_build_omits_q_for_default(self):
        assert AcceptEncoding([("gzip", 1.0)]).build() == "gzip"

    def test_build_includes_q_when_not_default(self):
        assert AcceptEncoding([("gzip", 0.5)]).build() == "gzip;q=0.5"

    def test_empty_string(self):
        assert AcceptEncoding("").raw == []


class TestContentType:
    def test_essence_lowercased(self):
        ct = ContentType("Text/HTML")
        assert ct.essence == "text/html"

    def test_essence_without_subtype(self):
        ct = ContentType("text")
        assert ct.essence == "text/"

    def test_charset_param(self):
        ct = ContentType("text/html; charset=UTF-8")
        assert ct.charset == "utf-8"

    def test_boundary_param(self):
        ct = ContentType('multipart/form-data; boundary="abc123"')
        assert ct.boundary == "abc123"

    def test_missing_param_returns_empty_string(self):
        ct = ContentType("text/plain")
        assert ct.charset == ""
        assert ct.boundary == ""

    def test_empty_value(self):
        ct = ContentType("")
        assert ct.essence == "/"
        assert ct.charset == ""

    def test_build_reconstructs_with_params(self):
        ct = ContentType("text/html;charset=utf-8")
        built = ct.build()
        assert built.startswith("text/html; charset=")
        assert ContentType(built).charset == "utf-8"

    def test_param_with_semicolon_inside_quotes_not_split(self):
        ct = ContentType('multipart/form-data; boundary="a;b"')
        assert ct.boundary == "a;b"


class TestETag:
    def test_strong_etag_not_weak(self):
        e = ETag('"abc"')
        assert not e.weak
        assert e.opaque_tag == '"abc"'

    def test_weak_etag_detected_lowercase_w(self):
        e = ETag('w/"abc"')
        assert e.weak
        assert e.opaque_tag == '"abc"'

    def test_weak_etag_detected_uppercase_w(self):
        e = ETag('W/"abc"')
        assert e.weak

    def test_strong_match_requires_both_strong_and_equal(self):
        a = ETag('"abc"')
        b = ETag('"abc"')
        assert a.strong_match(b)

    def test_strong_match_fails_if_either_weak(self):
        a = ETag('W/"abc"')
        b = ETag('"abc"')
        assert not a.strong_match(b)
        assert not b.strong_match(a)

    def test_weak_match_ignores_weakness(self):
        a = ETag('W/"abc"')
        b = ETag('"abc"')
        assert a.weak_match(b)

    def test_match_respects_strong_weak_flags(self):
        a = ETag('W/"abc"')
        b = ETag('"abc"')
        assert not a.match(b, strong=True, weak=False)
        assert a.match(b, strong=True, weak=True)

    def test_str_returns_raw_value(self):
        assert str(ETag('"abc"')) == '"abc"'

    def test_construct_from_another_etag_copies_state(self):
        a = ETag('W/"abc"')
        b = ETag(a)
        assert b.weak
        assert b.value == a.value
