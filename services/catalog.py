import re

from common.errors import BusinessError


class CatalogService:
    def __init__(self, repository):
        self.repo = repository

    async def active_rows(self, collection, query=None, *, limit=100, offset=0):
        """所有结构化资料同样核对活动版本，避免新版本构建时提前可见。"""
        output = []
        page = 0
        while len(output) < limit + offset:
            rows = await self.repo.find(
                collection, query, limit=200, offset=page * 200, sort=[("id", 1)]
            )
            if not rows:
                break
            documents = {
                row["id"]: row
                for row in await self.repo.find(
                    "documents",
                    {"id": {"$in": list({r.get("document_id") for r in rows})}},
                    limit=200,
                )
            }
            for row in rows:
                document = documents.get(row.get("document_id"))
                if row.get("manual") or (
                    document
                    and document.get("status") == "ready"
                    and document.get("active_version") == row.get("version_id")
                ):
                    output.append(row)
            page += 1
        return output[offset : offset + limit]

    async def courses(self, search="", limit=50, offset=0):
        query = (
            {
                "$or": [
                    {"name": {"$regex": re.escape(search), "$options": "i"}},
                    {"course_id": search},
                    {"aliases": search},
                ]
            }
            if search
            else {}
        )
        rows = await self.active_rows("courses", query, limit=limit, offset=offset)
        for row in rows:
            count = await self.repo.count(
                "documents",
                {
                    "course_id": row["course_id"],
                    "status": "ready",
                    "active_version": {"$ne": None},
                    "content_type": "lecture",
                },
            )
            row["material_status"] = "available" if count else "missing"
        return rows

    async def course(self, identifier):
        rows = await self.active_rows("courses", {"course_id": identifier}, limit=1)
        if not rows:
            raise BusinessError("course_not_found", "课程不存在或尚未激活", 404)
        rows[0]["documents"] = await self.repo.find(
            "documents", {"course_id": identifier, "status": {"$ne": "deleted"}}, limit=100
        )
        return rows[0]

    async def sections(self, course_id):
        documents = await self.repo.find(
            "documents", {"course_id": course_id, "status": "ready"}, limit=200
        )
        output = []
        for document in documents:
            rows = await self.repo.find(
                "sections",
                {"document_id": document["id"], "version_id": document.get("active_version")},
                limit=500,
                sort=[("section_index", 1)],
            )
            output.extend({**row, "source_name": document["source_name"]} for row in rows)
        return output

    @staticmethod
    def practice(row):
        """白名单响应，任何新增答案相关字段都不会随题目列表意外泄露。"""
        return {
            key: row[key]
            for key in (
                "document_id",
                "version_id",
                "images",
                "question_id",
                "code",
                "bank_id",
                "course_id",
                "question_type",
                "stem",
                "options",
                "knowledge_points",
                "source",
                "review_status",
            )
            if key in row
        }

    async def questions(
        self,
        course_id=None,
        code=None,
        question_type=None,
        knowledge_point=None,
        limit=50,
        offset=0,
    ):
        query = {}
        for name, value in (
            ("course_id", course_id),
            ("code", code),
            ("question_type", question_type),
            ("knowledge_points", knowledge_point),
        ):
            if value:
                query[name] = value
        rows = await self.active_rows("questions", query, limit=limit, offset=offset)
        return [self.practice(row) for row in rows]

    async def question(self, identifier, solution=False):
        rows = await self.active_rows("questions", {"question_id": identifier}, limit=1)
        if not rows:
            raise BusinessError("question_not_found", "题目不存在或原资料不可用", 404)
        return rows[0] if solution else self.practice(rows[0])
