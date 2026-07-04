import os
import asyncio
from email.utils import formatdate

from .errors import HTTPViolationError
from .models import Role, Request, Response
from .headers import CommaHeader

HOP_BY_HOP_HEADERS = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"})
FORBIDDEN_TRAILERS = frozenset({"transfer-encoding", "content-length", "host"})

async def finalize_request(request: Request):
    if request.protocol == "HTTP/1.1":
        host_values = request.headers["Host"]

        if host_values is not None and len(host_values) > 1:
            raise HTTPViolationError("multiple Host headers present")

        if not request.headers.get("Host"):
            raise HTTPViolationError("missing Host header")

    has_transfer_encoding = "Transfer-Encoding" in request.headers
    has_content_length = "Content-Length" in request.headers

    if has_transfer_encoding and has_content_length:
        raise HTTPViolationError("conflicting Transfer-Encoding and Content-Length headers")

    if has_transfer_encoding:
        transfer_encoding = CommaHeader(request.headers.get("Transfer-Encoding", ""))

        if not transfer_encoding.raw or transfer_encoding.raw[-1].lower() != "chunked":
            raise HTTPViolationError("Transfer-Encoding must end in chunked")

    content_length_values = request.headers["Content-Length"]

    if content_length_values and len(set(content_length_values)) > 1:
        raise HTTPViolationError("conflicting Content-Length values")

    if request.trailers is not None:
        for name in [n for n, _ in request.trailers.raw]:
            if name.lower() in FORBIDDEN_TRAILERS:
                request.trailers.remove(name)

async def finalize_response(response: Response, role: Role = Role.ORIGIN):
    if role != Role.ORIGIN:
        connection_header = CommaHeader(response.headers.get("Connection", ""))

        for extra in list(connection_header.raw):
            response.headers.remove(extra)

        for name in HOP_BY_HOP_HEADERS:
            response.headers.remove(name)

    if response.trailers is not None:
        for name in [n for n, _ in response.trailers.raw]:
            if name.lower() in FORBIDDEN_TRAILERS:
                response.trailers.remove(name)

    if response.status_code in (204, 304) or (100 <= response.status_code < 200):
        response.headers.remove("Content-Length")
        response.headers.remove("Transfer-Encoding")

    elif isinstance(response.body, bytes):
        response.headers.set("Content-Length", str(len(response.body)))
        response.headers.remove("Transfer-Encoding")

    elif isinstance(response.body, (os.PathLike, str)):
        path = response.body if isinstance(response.body, str) else os.fspath(response.body)
        file_size = (await asyncio.to_thread(os.stat, path)).st_size

        response.headers.set("Accept-Ranges", "bytes")

        if response.range is not None:
            start, end = response.range

            if start < 0 or start > end or start >= file_size:
                response.status_code = 416
                response.body = None
                response.headers.set("Content-Range", f"bytes */{file_size}")
                response.headers.set("Content-Length", "0")
            else:
                end = min(end, file_size - 1)
                response.status_code = 206
                response.headers.set("Content-Range", f"bytes {start}-{end}/{file_size}")
                response.headers.set("Content-Length", str(end - start + 1))
        else:
            response.headers.set("Content-Length", str(file_size))

        response.headers.remove("Transfer-Encoding")

    elif response.body is None:
        response.headers.set("Content-Length", "0")
        response.headers.remove("Transfer-Encoding")

    else:
        if response.protocol == "HTTP/1.1":
            response.headers.set("Transfer-Encoding", "chunked")
        else:
            response.headers.set("Connection", "close")

        response.headers.remove("Content-Length")

    response.headers.set("Date", formatdate(usegmt=True))
    response.headers.set("Server", "Momiji", override=False)
