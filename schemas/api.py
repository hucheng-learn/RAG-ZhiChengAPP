from typing import Literal

from pydantic import BaseModel, Field, model_validator

ContentType = Literal["lecture", "project", "catalog", "question_bank"]


class CatalogConfirmation(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)


class Attribution(BaseModel):
    course_id: str | None = Field(default=None, max_length=150)
    project_id: str | None = Field(default=None, max_length=150)

    @model_validator(mode="after")
    def one_scope(self):
        """显式归属只能选课程或项目；缺少归属交给人工确认。"""
        if self.course_id and self.project_id:
            raise ValueError("课程与项目归属只能选一个")
        return self


class SessionCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=100)
    course_ids: list[str] = Field(default_factory=list, max_length=10)
    project_id: str | None = None


class QueryRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=4000)
    course_ids: list[str] | None = Field(default=None, max_length=10)
    project_id: str | None = None
    content_type: Literal["lecture", "project", "question_bank"] | None = None
    question_type: str | None = Field(default=None, max_length=30)
    intent: Literal["auto", "qa", "exercise", "solution", "courses", "recommend"] = "auto"
    question_code: str | None = Field(default=None, max_length=200)


class CourseCreate(BaseModel):
    course_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,150}$")
    name: str = Field(min_length=1, max_length=200)
    series_id: str = Field(default="manual", max_length=150)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(default="", max_length=4000)
