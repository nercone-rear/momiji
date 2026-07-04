import os
import json
import gzip
import zlib
import base64
import rjsmin
import rcssmin
import ipaddress
import zstandard
import brotlicffi
import minify_html
from enum import Enum
from scour import scour
from typing import Any, Optional, Literal
from dataclasses import dataclass, field
from collections.abc import AsyncIterator

from .url import URL
from .headers import Headers, CommaHeader, ContentType

class Role(Enum):
    ORIGIN = "Origin"
    PROXY = "Proxy"
    GATEWAY = "Gateway"
    TUNNEL = "Tunnel"

@dataclass(kw_only=True)
class Message:
    client: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, int] = field(default_factory=lambda: (ipaddress.IPv4Address("0.0.0.0"), 0))

    protocol: Literal["HTTP/1.0", "HTTP/1.1"] = "HTTP/1.1"

    headers: Headers = field(default_factory=lambda: Headers({}))
    trailers: Optional[Headers] = None

    body: Optional[bytes | AsyncIterator[bytes] | os.PathLike] = None

    scheme: Literal["http", "https"] = "http"
    secure: bool = False

    early_data: bool = False

    compression: bool = True
    minification: bool = False

    compressed: bool = False
    minified: bool = False

    @property
    def text(self) -> str:
        return self.body.decode()

    @property
    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def has_real_body(self) -> bool:
        return self.body is not None and isinstance(self.body, bytes)

    def compress(self, encodings: Optional[str] = None):
        if not (self.compression and not self.compressed and self.body is not None):
            return

        content_encoding = CommaHeader(self.headers.get("Content-Encoding", ""))

        for encoding in encodings:
            if self.has_real_body:
                if encoding == "zstd":
                    self.body = zstandard.ZstdCompressor(level=3).compress(self.body)
                elif encoding == "br":
                    self.body = brotlicffi.compress(self.body, quality=4)
                elif encoding == "gzip":
                    self.body = gzip.compress(self.body, compresslevel=6)
                elif encoding == "deflate":
                    self.body = zlib.compress(self.body, level=6)
                else:
                    continue

            content_encoding.append(encoding)
            self.compressed = True

        self.headers.set("Content-Encoding", str(content_encoding))

    def decompress(self):
        if not (self.compression and self.compressed and self.body is not None):
            return

        content_encoding = CommaHeader(self.headers.get("Content-Encoding", ""))

        for encoding in reversed(content_encoding.raw):
            if self.has_real_body:
                if encoding == "zstd":
                    self.body = zstandard.ZstdDecompressor().decompress(self.body)
                elif encoding == "br":
                    self.body = brotlicffi.decompress(self.body)
                elif encoding == "gzip":
                    self.body = gzip.decompress(self.body)
                elif encoding == "deflate":
                    try:
                        self.body = zlib.decompress(self.body)
                    except zlib.error:
                        self.body = zlib.decompress(self.body, -zlib.MAX_WBITS)
                else:
                    break

            self.headers.remove("Content-Encoding")
            self.compressed = False

    def minify(self):
        if not (self.minification and not self.minified and self.body is not None):
            return

        content_type = ContentType(self.headers.get("Content-Type", ""))

        if self.has_real_body:
            try:
                if content_type.essence.startswith("text/html"):
                    self.body = minify_html.minify(self.body.decode("utf-8", errors="replace"), minify_js=True, minify_css=True, keep_comments=True, keep_html_and_head_opening_tags=True).encode("utf-8")

                elif content_type.essence.startswith("text/css"):
                    self.body = rcssmin.cssmin(self.body.decode("utf-8", errors="replace")).encode("utf-8")

                elif content_type.essence.startswith(("text/javascript", "application/javascript")):
                    self.body = rjsmin.jsmin(self.body.decode("utf-8", errors="replace")).encode("utf-8")

                elif content_type.essence.startswith("image/svg"):
                    options = scour.generateDefaultOptions()
                    options.newlines = False
                    options.shorten_ids = True
                    options.strip_comments = True

                    self.body = scour.scourString(self.body.decode("utf-8", errors="replace"), options).encode("utf-8")

                else:
                    return

                self.minified = True

            except Exception:
                pass

@dataclass
class Request(Message):
    method: Literal["GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"]
    target: str

    url: URL = field(init=False, repr=False)

    def __post_init__(self):
        authority = self.headers.get("Host", "")
        self.url = URL.from_target(self.target, self.scheme, authority)

    @property
    def is_websocket_upgrade(self) -> bool:
        upgrade           = self.headers.get("Upgrade", "").lower().strip()
        connection        = self.headers.get("Connection", "").lower()
        websocket_key     = self.headers.get("Sec-WebSocket-Key", "").strip()
        websocket_version = self.headers.get("Sec-WebSocket-Version", "").strip()

        try:
            key_valid = len(base64.b64decode(websocket_key, validate=True)) == 16
        except Exception:
            key_valid = False

        return (self.method == "GET") and (upgrade == "websocket") and ("upgrade" in connection) and (websocket_version == "13") and key_valid

@dataclass
class Response(Message):
    status_code: int = 200

    range: Optional[tuple[int, int]] = field(default=None)
