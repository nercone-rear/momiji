from __future__ import annotations

from typing import Optional, Union, TypeVar

from .constants import Characters

T = TypeVar("T")

TOKEN_CHARS = frozenset("!#$%&'*+-.^_`|~") | Characters.DIGIT | Characters.LOWER | Characters.UPPER
FORBIDDEN_VALUE_CHARS = {chr(c) for c in range(0x20) if c != 0x09} | {chr(0x7F)}

def is_valid_token(s: str) -> bool:
    return len(s) > 0 and all(c in TOKEN_CHARS for c in s)

def split_unquoted(value: str, delim: str) -> list[str]:
    parts = []
    current = []
    in_quotes = False
    i = 0
    n = len(value)

    while i < n:
        c = value[i]

        if in_quotes:
            if c == "\\" and i + 1 < n:
                current.append(c)
                current.append(value[i + 1])
                i += 2
                continue

            if c == '"':
                in_quotes = False

            current.append(c)
        else:
            if c == '"':
                in_quotes = True
                current.append(c)
            elif c == delim:
                parts.append("".join(current))
                current = []
            else:
                current.append(c)

        i += 1

    parts.append("".join(current))
    return parts

def quote(value: str) -> str:
    if not value or any(c in TOKEN_CHARS for c in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return value

def unquote(value: str) -> str:
    value = value.strip()

    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        result = []
        i = 0

        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                result.append(inner[i + 1])
                i += 2
            else:
                result.append(inner[i])
                i += 1

        return "".join(result)

    return value

class Headers:
    def __init__(self, value: str | list[tuple[str, list[str]]]):
        if isinstance(value, (str, bytes)):
            self.raw = Headers.parse(value).raw
        elif isinstance(value, list):
            self.raw = value
        else:
            self.raw = []

    def __str__(self) -> str:
        return self.build()

    def find(self, key: str) -> Optional[int]:
        key_lower = key.lower()

        for i, (name, _) in enumerate(self.raw):
            if name.lower() == key_lower:
                return i

        return None

    def __getitem__(self, key: str) -> Optional[list[str]]:
        idx = self.find(key)
        return None if idx is None else self.raw[idx][1]

    def __setitem__(self, key: str, value: str | list[str]):
        self.set(key, value, override=True)

    def __contains__(self, item: str) -> bool:
        return self.find(item) is not None

    def items(self) -> list[tuple[str, str]]:
        return [(name, v) for name, values in self.raw for v in values]

    def get(self, key: str, default: Optional[T] = None) -> Optional[str | T]:
        idx = self.find(key)
        return default if idx is None else ", ".join(self.raw[idx][1])

    def set(self, key: str, value: str | list[str], override: bool = True):
        values = [value] if isinstance(value, str) else list(value)
        idx = self.find(key)

        if idx is not None:
            if override:
                self.raw[idx] = (key, values)
            return

        self.raw.append((key, values))

    def append(self, key: str, value: str):
        idx = self.find(key)

        if idx is not None:
            self.raw[idx][1].append(value)
        else:
            self.raw.append((key, [value]))

    def remove(self, key: str):
        idx = self.find(key)

        if idx is not None:
            del self.raw[idx]

    @classmethod
    def parse(cls, value: str) -> "Headers":
        raw: list[tuple[str, list[str]]] = []

        if not value:
            return cls(raw)

        lines = value.split("\r\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]

        for line in lines:
            if line[:1] in (" ", "\t"):
                raise ValueError("obsolete line folding is not supported")

            if ":" not in line:
                raise ValueError("malformed header line")

            name, _, raw_value = line.partition(":")

            if not is_valid_token(name):
                raise ValueError(f"invalid header name: {name!r}")

            header_value = raw_value.strip(" \t")

            if any(c in FORBIDDEN_VALUE_CHARS for c in header_value):
                raise ValueError(f"invalid character in header value: {header_value!r}")

            found = False
            for existing_name, values in raw:
                if existing_name.lower() == name.lower():
                    values.append(header_value)
                    found = True
                    break

            if not found:
                raw.append((name, [header_value]))

        return cls(raw)

    def build(self) -> str:
        return "".join(f"{name}: {value}\r\n" for name, value in self.items())

class CommaHeader:
    def __init__(self, value: str | list[str]):
        if isinstance(value, (str, bytes)):
            self.raw = CommaHeader.parse(value).raw
        elif isinstance(value, list):
            self.raw = value

    def __str__(self) -> str:
        return self.build()

    def __contains__(self, item: str) -> bool:
        return item in self.raw

    def set(self, value: str | list[str]):
        if isinstance(value, str):
            self.raw = [value]
        elif isinstance(value, list):
            self.raw = value

    def append(self, value: str):
        self.raw.append(value)

    def remove(self, value: str):
        self.raw.remove(value)

    @classmethod
    def parse(cls, value: str) -> "CommaHeader":
        return cls([v.strip() for v in value.split(",") if v.strip()])

    def build(self) -> str:
        return ", ".join(self.raw)

class Link:
    def __init__(self, value: str | list[tuple[str, dict[str, str]]]):
        if isinstance(value, (str, bytes)):
            self.raw = Link.parse(value).raw
        elif isinstance(value, list):
            self.raw = value

    @classmethod
    def parse(cls, value: str) -> "Link":
        raw: list[tuple[str, dict[str, str]]] = []

        if not value:
            return cls(raw)

        for segment in split_unquoted(value, ","):
            segment = segment.strip()

            if not segment.startswith("<"):
                continue

            end = segment.find(">")
            if end == -1:
                continue

            uri = segment[1:end]
            params: dict[str, str] = {}
            remainder = segment[end + 1:]

            for param_part in split_unquoted(remainder, ";"):
                param_part = param_part.strip()

                if not param_part or "=" not in param_part:
                    continue

                name, _, raw_param_value = param_part.partition("=")
                params[name.strip().lower()] = unquote(raw_param_value.strip())

            raw.append((uri, params))

        return cls(raw)

    def build(self) -> str:
        parts = []

        for uri, params in self.raw:
            part = f"<{uri}>"

            for name, value in params.items():
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                part += f'; {name}="{escaped}"'

            parts.append(part)

        return ", ".join(parts)

class AcceptEncoding:
    def __init__(self, value: str | list[tuple[str, float]]):
        if isinstance(value, (str, bytes)):
            self.raw = AcceptEncoding.parse(value).raw
        elif isinstance(value, list):
            self.raw = value

    @classmethod
    def parse(cls, value: str) -> "AcceptEncoding":
        raw: list[tuple[str, float]] = []

        for segment in value.split(","):
            segment = segment.strip()

            if not segment:
                continue

            parts = segment.split(";")
            coding = parts[0].strip().lower()
            q = 1.0

            for param in parts[1:]:
                param = param.strip()

                if param.lower().startswith("q="):
                    try:
                        q = float(param[2:].strip())
                    except ValueError:
                        q = 1.0

            raw.append((coding, q))

        return cls(raw)

    def build(self) -> str:
        parts = []

        for coding, q in self.raw:
            if q == 1.0:
                parts.append(coding)
            else:
                parts.append(f"{coding};q={q:.3g}")

        return ", ".join(parts)

class ContentType:
    def __init__(self, value: str):
        self.value = value

    @property
    def essence(self) -> str:
        type_, subtype, _ = self.parse()
        return f"{type_}/{subtype}"

    @property
    def charset(self) -> str:
        _, _, params = self.parse()
        return params.get("charset", "").lower()

    @property
    def boundary(self) -> str:
        _, _, params = self.parse()
        return params.get("boundary", "")

    def parse(self) -> tuple[str, str, dict[str, str]]:
        if not self.value:
            return "", "", {}

        media_type_part, _, param_str = self.value.partition(";")
        media_type_part = media_type_part.strip()

        if "/" in media_type_part:
            type, _, subtype = media_type_part.partition("/")
        else:
            type, subtype = media_type_part, ""

        type = type.strip().lower()
        subtype = subtype.strip().lower()

        params: dict[str, str] = {}

        if param_str:
            for param_part in split_unquoted(param_str, ";"):
                param_part = param_part.strip()

                if not param_part or "=" not in param_part:
                    continue

                name, _, raw_value = param_part.partition("=")
                params[name.strip().lower()] = unquote(raw_value.strip())

        return type, subtype, params

    def build(self) -> str:
        type_, subtype, params = self.parse()
        result = f"{type_}/{subtype}"

        for name, value in params.items():
            result += f"; {name}={quote(value)}"

        return result

class ETag:
    def __init__(self, value: Union[str, "ETag"]):
        if isinstance(value, str):
            self.value = value
            self.weak = self.value.startswith(("w/", "W/"))
        elif isinstance(value, ETag):
            self.value = value.value
            self.weak = value.weak

    def __str__(self) -> str:
        return self.value

    @property
    def opaque_tag(self) -> str:
        if self.weak:
            return self.value[2:]

        return self.value

    def match(self, other: Union[str, "ETag"], strong: bool = True, weak: bool = True) -> bool:
        if strong and self.strong_match(other):
            return True

        if weak and self.weak_match(other):
            return True

        return False

    def strong_match(self, other: Union[str, "ETag"]) -> bool:
        return (not self.weak) and (not ETag(other).weak) and (self.opaque_tag == ETag(other).opaque_tag)

    def weak_match(self, other: str | "ETag") -> bool:
        return self.opaque_tag == ETag(other).opaque_tag
