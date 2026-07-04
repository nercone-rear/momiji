class HTTPError(Exception):
    """An HTTP-level error to be reported to the client with a status code."""

    def __init__(self, code: int = 400, message: str = "Bad Request"):
        self.code = code
        self.message = message
        super().__init__(message)

class HTTPViolationError(Exception):
    """Raised when a message violates the HTTP/1.1 specification."""

class HTTPReportedViolationError(HTTPError, HTTPViolationError):
    """A specification violation that is reported to the client with a status code."""

class WebSocketProtocolError(Exception):
    """Raised when a WebSocket frame violates the framing protocol, carrying a close code."""

    def __init__(self, code: int, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(message)
