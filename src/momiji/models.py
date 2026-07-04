import os
import json
import gzip
import zlib
import xxhash
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
from .headers import Headers, CommaHeader, ContentType, ETag, Cookie, SetCookie

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

    body: Optional[bytes | str | AsyncIterator[bytes] | os.PathLike] = None

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

    def compress(self, encodings: Optional[str] = None, *, max_offload_filesize: int = 32768):
        if not (self.compression and not self.compressed and self.body is not None):
            return

        content_encoding = CommaHeader(self.headers.get("Content-Encoding", ""))

        if isinstance(self.body, bytes):
            for encoding in encodings:
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

        elif isinstance(self.body, (str, os.PathLike)):
            filepath = self.body
            filesize = os.stat(filepath).st_size

            if 0 < filesize <= max_offload_filesize:
                with open(filepath, "rb") as f:
                    self.body = f.read()

                self.compress(encodings, max_offload_filesize=max_offload_filesize)

    def decompress(self, *, max_offload_filesize: int = 32768):
        if not (self.compression and self.compressed and self.body is not None):
            return

        content_encoding = CommaHeader(self.headers.get("Content-Encoding", ""))

        if isinstance(self.body, bytes):
            for encoding in reversed(content_encoding.raw):
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

        elif isinstance(self.body, (str, os.PathLike)):
            filepath = self.body
            filesize = os.stat(filepath).st_size

            if 0 < filesize <= max_offload_filesize:
                with open(filepath, "rb") as f:
                    self.body = f.read()

                self.decompress()

    def minify(self, *, max_offload_filesize: int = 32768):
        if not (self.minification and not self.minified and self.body is not None):
            return

        content_type = ContentType(self.headers.get("Content-Type", ""))

        if isinstance(self.body, bytes):
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

        elif isinstance(self.body, (str, os.PathLike)):
            filepath = self.body
            filesize = os.stat(filepath).st_size

            if 0 < filesize <= max_offload_filesize:
                with open(filepath, "rb") as f:
                    self.body = f.read()

                self.minify()

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
        upgrade = self.headers.get("Upgrade", "").lower().strip()
        connection_tokens = CommaHeader(self.headers.get("Connection", "")).raw

        return (self.method == "GET") and (upgrade == "websocket") and any(t.strip().lower() == "upgrade" for t in connection_tokens)

    @property
    def cookies(self) -> Cookie:
        return Cookie(self.headers.get("Cookie", ""))

@dataclass
class Response(Message):
    status_code: int = 200

    range: Optional[tuple[int, int]] = field(default=None)

    @property
    def etag(self) -> ETag:
        if isinstance(self.body, bytes):
            return ETag(f'"{xxhash.xxh3_128(self.body).hexdigest()}"')
        elif isinstance(self.body, os.PathLike):
            stat = os.stat(self.body)
            return ETag(f'"{int(stat.st_mtime_ns):x}-{stat.st_size:x}"')

    def set_cookie(self, name: str, value: str, *, expires: Optional[str] = None, max_age: Optional[int] = None, domain: Optional[str] = None, path: Optional[str] = "/", secure: bool = False, httponly: bool = False, samesite: Optional[Literal["Strict", "Lax", "None"]] = None):
        self.headers.append("Set-Cookie", str(SetCookie(name, value, expires=expires, max_age=max_age, domain=domain, path=path, secure=secure, httponly=httponly, samesite=samesite)))

    def delete_cookie(self, name: str, *, domain: Optional[str] = None, path: Optional[str] = "/", secure: bool = False, httponly: bool = False, samesite: Optional[Literal["Strict", "Lax", "None"]] = None):
        self.set_cookie(name, "", max_age=0, domain=domain, path=path, secure=secure, httponly=httponly, samesite=samesite)
