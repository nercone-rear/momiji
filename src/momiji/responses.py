import os
import json
from typing import Optional
from pathlib import Path

from .models import Response
from .headers import Headers

class PlainTextResponse(Response):
    def __init__(self, content: str, status_code: int = 200, *, headers: Optional[Headers] = None, compression: bool = True, range: Optional[tuple[int, int]] = None):
        self.body = content.encode()
        self.status_code = status_code
        self.headers = headers or Headers({})
        self.compression = compression
        self.minification = False
        self.range = range

        self.headers.set("Content-Type", "text/plain")

class HTMLResponse(Response):
    def __init__(self, content: str, status_code: int = 200, *, headers: Optional[Headers] = None, compression: bool = True, minification: bool = False, range: Optional[tuple[int, int]] = None):
        self.body = content.encode()
        self.status_code = status_code
        self.headers = headers or Headers({})
        self.compression = compression
        self.minification = minification
        self.range = range

        self.headers.set("Content-Type", "text/html")

class JSONResponse(Response):
    def __init__(self, content: list | dict, status_code: int = 200, *, headers: Optional[Headers] = None, compression: bool = True, range: Optional[tuple[int, int]] = None):
        self.body = json.dumps(content).encode()
        self.status_code = status_code
        self.headers = headers or Headers({})
        self.compression = compression
        self.minification = False
        self.range = range

        self.headers.set("Content-Type", "application/json")

class FileResponse(Response):
    def __init__(self, path: os.PathLike | Path, status_code: int = 200, *, headers: Optional[Headers] = None, content_type: Optional[str] = None, compression: bool = True, minification: bool = False, range: Optional[tuple[int, int]] = None):
        self.body = str(path) if isinstance(path, Path) else path
        self.status_code = status_code
        self.headers = headers or Headers({})
        self.compression = compression
        self.minification = minification
        self.range = range

        if content_type is not None:
            self.headers.set("Content-Type", content_type)

class RedirectResponse(Response):
    def __init__(self, url: str, status_code: int = 307, *, headers: Optional[Headers] = None):
        self.body = None
        self.status_code = status_code
        self.headers = headers or Headers({})
        self.compression = False
        self.minification = False
        self.range = None

        self.headers.set("Location", url)
