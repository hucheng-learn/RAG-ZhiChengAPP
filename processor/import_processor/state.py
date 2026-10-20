from typing import TypedDict


class ImportState(TypedDict, total=False):
    task_id: str
    version_id: str
    chunk_count: int
    record_count: int
    catalog_needs_review: bool
