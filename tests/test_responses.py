import json

from momiji.responses import PlainTextResponse, HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from momiji.headers import Headers


class TestPlainTextResponse:
    def test_encodes_body_and_sets_content_type(self):
        resp = PlainTextResponse("hello")
        assert resp.body == b"hello"
        assert resp.headers.get("Content-Type") == "text/plain"
        assert resp.status_code == 200

    def test_custom_status_code(self):
        resp = PlainTextResponse("not found", status_code=404)
        assert resp.status_code == 404

    def test_preserves_supplied_headers(self):
        headers = Headers([("X-Custom", ["1"])])
        resp = PlainTextResponse("hi", headers=headers)
        assert resp.headers.get("X-Custom") == "1"
        assert resp.headers.get("Content-Type") == "text/plain"

    def test_minification_always_disabled(self):
        resp = PlainTextResponse("hi")
        assert resp.minification is False


class TestHTMLResponse:
    def test_encodes_body_and_sets_content_type(self):
        resp = HTMLResponse("<p>hi</p>")
        assert resp.body == b"<p>hi</p>"
        assert resp.headers.get("Content-Type") == "text/html"

    def test_minification_flag_configurable(self):
        resp = HTMLResponse("<p>hi</p>", minification=True)
        assert resp.minification is True

    def test_minification_defaults_false(self):
        resp = HTMLResponse("<p>hi</p>")
        assert resp.minification is False

    def test_range_forwarded(self):
        resp = HTMLResponse("<p>hi</p>", range=(0, 1))
        assert resp.range == (0, 1)


class TestJSONResponse:
    def test_serializes_dict(self):
        resp = JSONResponse({"a": 1})
        assert json.loads(resp.body) == {"a": 1}
        assert resp.headers.get("Content-Type") == "application/json"

    def test_serializes_list(self):
        resp = JSONResponse([1, 2, 3])
        assert json.loads(resp.body) == [1, 2, 3]

    def test_minification_always_disabled(self):
        resp = JSONResponse({})
        assert resp.minification is False


class TestFileResponse:
    def test_path_converted_to_string(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("hi")
        resp = FileResponse(path)
        assert resp.body == str(path)

    def test_os_pathlike_kept_as_is_when_not_pathlib_path(self, tmp_path):
        import os
        path = tmp_path / "file.txt"
        path.write_text("hi")
        fspath_str = os.fspath(path)
        resp = FileResponse(fspath_str)
        assert resp.body == fspath_str

    def test_content_type_override(self, tmp_path):
        path = tmp_path / "file.bin"
        path.write_text("hi")
        resp = FileResponse(path, content_type="application/octet-stream")
        assert resp.headers.get("Content-Type") == "application/octet-stream"

    def test_content_type_guessed_from_extension(self, tmp_path):
        path = tmp_path / "file.css"
        path.write_text("hi")
        resp = FileResponse(path)
        assert resp.headers.get("Content-Type") == "text/css"

    def test_no_content_type_when_extension_unknown(self, tmp_path):
        path = tmp_path / "file.unknownext"
        path.write_text("hi")
        resp = FileResponse(path)
        assert resp.headers.get("Content-Type") is None

    def test_range_forwarded(self, tmp_path):
        path = tmp_path / "file.bin"
        path.write_text("hi")
        resp = FileResponse(path, range=(0, 1))
        assert resp.range == (0, 1)


class TestRedirectResponse:
    def test_sets_location_header(self):
        resp = RedirectResponse("/new-location")
        assert resp.headers.get("Location") == "/new-location"

    def test_default_status_is_307(self):
        resp = RedirectResponse("/x")
        assert resp.status_code == 307

    def test_custom_status_code(self):
        resp = RedirectResponse("/x", status_code=301)
        assert resp.status_code == 301

    def test_body_is_none(self):
        resp = RedirectResponse("/x")
        assert resp.body is None

    def test_compression_and_minification_disabled(self):
        resp = RedirectResponse("/x")
        assert resp.compression is False
        assert resp.minification is False
