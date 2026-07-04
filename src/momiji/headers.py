from __future__ import annotations

from typing import Optional, TypeVar

T = TypeVar("T")

class Headers:
    def __init__(self, value: str | list[tuple[str, list[str]]]):
        if isinstance(value, (str, bytes)):
            self.raw = Headers.parse(value).raw
        elif isinstance(value, list):
            self.raw = value

    def __str__(self) -> str:
        return self.build()

    def __getitem__(self, key: str) -> Optional[list[str]]:
        ...

    def __setitem__(self, key: str, value: str | list[str]):
        ...

    def __contains__(self, item: str) -> bool:
        ...

    def items(self) -> list[tuple[str, str]]:
        ...

    def get(self, key: str, default: Optional[T] = None) -> Optional[str | T]:
        ...

    def set(self, key: str, value: str | list[str], override: bool = True):
        ...

    def append(self, key: str, value: str):
        ...

    def remove(self, key: str):
        ...

    @classmethod
    def parse(cls, value: str) -> "Headers":
        ...

    def build(self) -> str:
        ...

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
        ...

    def build(self) -> str:
        ...

class AcceptEncoding:
    def __init__(self, value: str | dict[str, float]):
        if isinstance(value, (str, bytes)):
            self.raw = AcceptEncoding.parse(value).raw
        elif isinstance(value, list):
            self.raw = value

    @classmethod
    def parse(cls, value: str) -> "AcceptEncoding":
        ...

    def build(self) -> str:
        ...

class ContentType:
    def __init__(self, value: str):
        self.value = value

    @property
    def essence(self) -> str:
        ...

    @property
    def charset(self) -> str:
        ...

    @property
    def boundary(self) -> str:
        ...

    def parse(self) -> dict[str, str, str]:
        ...

    def build(self) -> str:
        ...

class ETag:
    def __init__(self, value: str):
        self.value = value

    def __str__(self) -> str:
        return self.value

    def match(self, other: str | "ETag", strong: bool = True, weak: bool = True) -> bool:
        if strong and self.strong_match(other):
            return True

        if weak and self.weak_match(other):
            return True

        return False

    def strong_match(self, other: str | "ETag") -> bool:
        ...

    def weak_match(self, other: str | "ETag") -> bool:
        ...
