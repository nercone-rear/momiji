from momiji.url import URL

def test_origin_form():
    url = URL.from_target("/foo/bar?x=1&y=2#frag", "http", "example.com:8080")
    assert url.path == "/foo/bar"
    assert url.query == "x=1&y=2"
    assert url.fragment == "frag"
    assert url.host == "example.com"
    assert url.port == 8080

def test_origin_form_no_port():
    url = URL.from_target("/foo", "http", "example.com")
    assert url.host == "example.com"
    assert url.port is None

def test_origin_form_ipv6_host():
    url = URL.from_target("/foo", "http", "[::1]:9090")
    assert url.host == "::1"
    assert url.port == 9090

def test_absolute_form():
    url = URL.from_target("http://example.com:81/x?y=1", "http", "")
    assert url.scheme == "http"
    assert url.host == "example.com"
    assert url.port == 81
    assert url.path == "/x"
    assert url.query == "y=1"

def test_absolute_form_no_path():
    url = URL.from_target("http://example.com", "http", "")
    assert url.path == "/"

def test_authority_form_connect():
    url = URL.from_target("example.com:443", "http", "")
    assert url.host == "example.com"
    assert url.port == 443

def test_asterisk_form():
    url = URL.from_target("*", "http", "example.com")
    assert str(url) == "*"

def test_params():
    url = URL(scheme="http", host="x", port=None, path="/", query="a=1&a=2&b=hello%20world", fragment="")
    assert url.params == {"a": ["1", "2"], "b": ["hello world"]}

def test_netloc():
    assert URL(scheme="http", host="example.com", port=None, path="/", query="", fragment="").netloc == "example.com"
    assert URL(scheme="http", host="example.com", port=80, path="/", query="", fragment="").netloc == "example.com:80"
    assert URL(scheme="http", host="::1", port=443, path="/", query="", fragment="").netloc == "[::1]:443"

def test_str_round_trip():
    url = URL(scheme="http", host="example.com", port=8080, path="/x", query="a=1", fragment="frag")
    assert str(url) == "http://example.com:8080/x?a=1#frag"

def test_missing_host_is_lenient():
    url = URL.from_target("/foo", "http", "")
    assert url.host == ""
    assert url.port is None
