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
    "RESOURCE",
    "SKILL",
]


class SearchResultRead(BaseModel):
    entity_type: SearchEntityType
    id: str
    # The project the record belongs to (the project itself for projects), for opening it.
    project_id: str | None = None
    title: str
    subtitle: str | None = None
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResultRead]