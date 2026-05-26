from typing import Literal

from pydantic import BaseModel


SearchEntityType = Literal[
    "PORTFOLIO",
    "PROGRAM",
    "PROJECT",
    "TASK",
    "RISK",
    "ISSUE",
    "ASSUMPTION",
    "DECISION",
    "SPRINT",
]


class SearchResultRead(BaseModel):
    entity_type: SearchEntityType
    id: str
    title: str
    subtitle: str | None = None
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResultRead]