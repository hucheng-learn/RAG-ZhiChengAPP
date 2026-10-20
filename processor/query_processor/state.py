from typing import TypedDict


class QueryState(TypedDict, total=False):
    run: dict
    question: str
    intent: str
    filters: dict
    evidence: list[dict]
    result: dict
