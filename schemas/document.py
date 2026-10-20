from typing import Literal

from pydantic import BaseModel, Field


class SourceLocation(BaseModel):
    format: Literal["markdown", "docx", "normalized"]
    unit: Literal["line", "block"]
    # 一基编号，end_exclusive 不包含在范围内；DOCX block 统计段落和表格。
    start: int = Field(ge=1)
    end_exclusive: int = Field(ge=1)
    heading_start: int | None = None


class Section(BaseModel):
    section_id: str
    parent_section_id: str | None = None
    document_id: str
    version_id: str
    section_index: int
    title: str
    title_path: list[str]
    heading_level: int
    body: str
    source: SourceLocation


class ParsedDocument(BaseModel):
    parser_version: str
    sections: list[Section]
    warnings: list[str] = Field(default_factory=list)
