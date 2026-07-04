from typing import Optional
from dataclasses import dataclass

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
        ...

    @property
    def params(self) -> dict[str, list[str]]:
        ...

    @property
    def netloc(self) -> str:
        ...

    def __str__(self) -> str:
        ...
