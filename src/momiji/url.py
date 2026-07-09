from typing import Optional
from dataclasses import dataclass
from urllib.parse import parse_qs

def parse_authority(authority: str) -> tuple[str, Optional[int]]:
    authority = authority.strip()

    if not authority:
        return "", None

    if authority.startswith("["):
        end = authority.find("]")
        if end == -1:
            return authority, None

        host = authority[1:end]
        rest = authority[end + 1:]

        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])

        return host, None

    if ":" in authority:
        host, _, port = authority.rpartition(":")
        if port.isdigit():
            return host, int(port)

    return authority, None

def split_path_query_fragment(remainder: str) -> tuple[str, str, str]:
    fragment = ""
    if "#" in remainder:
        remainder, _, fragment = remainder.partition("#")

    query = ""
    if "?" in remainder:
        path, _, query = remainder.partition("?")
    else:
        path = remainder

    return path, query, fragment

@dataclass
class URL:
    scheme: str
    host: str
    port: Optional[int]
    path: str
    query: str
    fragment: str

    @classmethod
    def from_target(cls, target: str, scheme: str = "http", authority: str = "") -> "URL":
        if target == "*":
            host, port = parse_authority(authority)
            return cls(scheme=scheme, host=host, port=port, path="*", query="", fragment="")

        if "://" in target:
            head, _, remainder = target.partition("://")

            end = len(remainder)
            for sep in ("/", "?", "#"):
                idx = remainder.find(sep)
                if idx != -1:
                    end = min(end, idx)

            raw_authority = remainder[:end]
            rest = remainder[end:]

            host, port = parse_authority(raw_authority)
            path, query, fragment = split_path_query_fragment(rest)

            if not path:
                path = "/"

            return cls(scheme=head, host=host, port=port, path=path, query=query, fragment=fragment)

        if target.startswith("/"):
            path, query, fragment = split_path_query_fragment(target)
            host, port = parse_authority(authority)
            return cls(scheme=scheme, host=host, port=port, path=path, query=query, fragment=fragment)

        host, port = parse_authority(target)
        return cls(scheme=scheme, host=host, port=port, path="", query="", fragment="")

    @property
    def params(self) -> dict[str, list[str]]:
        return parse_qs(self.query, keep_blank_values=True)

    @property
    def netloc(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host

        if self.port is not None:
            return f"{host}:{self.port}"

        return host

    def __str__(self) -> str:
        if self.path == "*":
            return "*"

        if self.host:
            result = f"{self.scheme}://{self.netloc}{self.path}"
        else:
            result = self.path

        if self.query:
            result += f"?{self.query}"

        if self.fragment:
            result += f"#{self.fragment}"

        return result
