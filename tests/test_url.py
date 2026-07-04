from momiji.url import URL, parse_authority, split_path_query_fragment


class TestParseAuthority:
    def test_host_only(self):
        assert parse_authority("example.com") == ("example.com", None)

    def test_host_and_port(self):
        assert parse_authority("example.com:8080") == ("example.com", 8080)

    def test_empty_authority(self):
        assert parse_authority("") == ("", None)

    def test_ipv6_without_port(self):
        assert parse_authority("[::1]") == ("::1", None)

    def test_ipv6_with_port(self):
        assert parse_authority("[::1]:8080") == ("::1", 8080)

    def test_non_numeric_port_ignored(self):
        assert parse_authority("example.com:abc") == ("example.com:abc", None)

    def test_strips_whitespace(self):
        assert parse_authority("  example.com:80  ") == ("example.com", 80)


class TestSplitPathQueryFragment:
    def test_path_only(self):
        assert split_path_query_fragment("/a/b") == ("/a/b", "", "")

    def test_path_and_query(self):
        assert split_path_query_fragment("/a?x=1") == ("/a", "x=1", "")

    def test_path_query_and_fragment(self):
        assert split_path_query_fragment("/a?x=1#frag") == ("/a", "x=1", "frag")

    def test_fragment_only(self):
        assert split_path_query_fragment("/a#frag") == ("/a", "", "frag")

    def test_query_containing_hash_after_split(self):
        # fragment is separated first, so a literal '#' inside what looks like
        # a query is always treated as starting the fragment (per RFC 3986,
        # '#' is not valid unencoded in a query and always begins a fragment).
        assert split_path_query_fragment("/a?x=1&y=2#f") == ("/a", "x=1&y=2", "f")


class TestURLFromTargetOriginForm:
    def test_simple_path(self):
        url = URL.from_target("/foo", authority="example.com")
        assert url.scheme == "http"
        assert url.host == "example.com"
        assert url.port is None
        assert url.path == "/foo"
        assert url.query == ""
        assert url.fragment == ""

    def test_path_with_query(self):
        url = URL.from_target("/foo?a=1&b=2", authority="example.com")
        assert url.path == "/foo"
        assert url.query == "a=1&b=2"

    def test_path_with_query_and_fragment(self):
        url = URL.from_target("/foo?a=1#frag", authority="example.com")
        assert url.path == "/foo"
        assert url.query == "a=1"
        assert url.fragment == "frag"

    def test_authority_with_port(self):
        url = URL.from_target("/", authority="example.com:8080")
        assert url.host == "example.com"
        assert url.port == 8080

    def test_custom_scheme_propagates(self):
        url = URL.from_target("/", scheme="https", authority="example.com")
        assert url.scheme == "https"


class TestURLFromTargetAbsoluteForm:
    def test_absolute_url_with_path(self):
        url = URL.from_target("http://example.com/foo?bar#baz")
        assert url.scheme == "http"
        assert url.host == "example.com"
        assert url.path == "/foo"
        assert url.query == "bar"
        assert url.fragment == "baz"

    def test_absolute_url_without_path_defaults_to_slash(self):
        url = URL.from_target("http://example.com")
        assert url.path == "/"

    def test_absolute_url_with_port(self):
        url = URL.from_target("http://example.com:8080/foo")
        assert url.host == "example.com"
        assert url.port == 8080

    def test_absolute_url_with_ipv6_host(self):
        url = URL.from_target("http://[::1]:8080/foo")
        assert url.host == "::1"
        assert url.port == 8080

    def test_absolute_url_query_only(self):
        url = URL.from_target("http://example.com?x=1")
        assert url.path == "/"
        assert url.query == "x=1"


class TestURLFromTargetAuthorityForm:
    def test_connect_style_target(self):
        url = URL.from_target("example.com:443")
        assert url.host == "example.com"
        assert url.port == 443
        assert url.path == ""


class TestURLFromTargetAsteriskForm:
    def test_asterisk_form(self):
        url = URL.from_target("*", authority="example.com:80")
        assert url.path == "*"
        assert url.host == "example.com"
        assert url.port == 80

    def test_asterisk_str_returns_asterisk(self):
        url = URL.from_target("*", authority="example.com")
        assert str(url) == "*"


class TestURLParams:
    def test_params_parses_query_string(self):
        url = URL.from_target("/foo?a=1&b=2&a=3", authority="example.com")
        assert url.params == {"a": ["1", "3"], "b": ["2"]}

    def test_params_keeps_blank_values(self):
        url = URL.from_target("/foo?a=&b=1", authority="example.com")
        assert url.params == {"a": [""], "b": ["1"]}

    def test_params_empty_query(self):
        url = URL.from_target("/foo", authority="example.com")
        assert url.params == {}


class TestURLNetloc:
    def test_netloc_without_port(self):
        url = URL.from_target("/", authority="example.com")
        assert url.netloc == "example.com"

    def test_netloc_with_port(self):
        url = URL.from_target("/", authority="example.com:8080")
        assert url.netloc == "example.com:8080"

    def test_netloc_wraps_ipv6_in_brackets(self):
        url = URL.from_target("http://[::1]:80/")
        assert url.netloc == "[::1]:80"


class TestURLStr:
    def test_str_with_host_reconstructs_absolute_url(self):
        url = URL.from_target("http://example.com/foo?bar#baz")
        assert str(url) == "http://example.com/foo?bar#baz"

    def test_str_without_host_reconstructs_origin_form(self):
        url = URL.from_target("/foo?bar", authority="")
        assert str(url) == "/foo?bar"

    def test_str_without_query_or_fragment(self):
        url = URL.from_target("/foo", authority="")
        assert str(url) == "/foo"
