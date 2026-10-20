import asyncio
from functools import partial

from pymongo import ASCENDING, ReturnDocument


class MongoRepository:
    """限制集合白名单；所有同步数据库操作交给共享有界线程池执行。"""

    collections = frozenset(
        {
            "documents",
            "versions",
            "sections",
            "chunks",
            "tasks",
            "courses",
            "series",
            "questions",
            "sessions",
            "messages",
        }
    )

    def __init__(self, client, database: str, executor):
        if database != "zhichengapp":
            raise ValueError("仅允许访问知成专用数据库 zhichengapp")
        self.db = client[database]
        self.executor = executor

    async def call(self, operation, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(operation, *args, **kwargs)
        )

    def collection(self, name):
        if name not in self.collections:
            raise ValueError("未知业务集合")
        return self.db[name]

    async def initialize(self):
        """只在 zhichengapp 内建索引；幂等索引为重复导入和消息唯一性兜底。"""
        for name in self.collections:
            await self.call(self.collection(name).create_index, "id", unique=True)
        await self.call(self.collection("documents").create_index, "import_key", unique=True)
        for name in ("sections", "chunks", "questions", "courses", "series"):
            await self.call(
                self.collection(name).create_index,
                [("document_id", ASCENDING), ("version_id", ASCENDING)],
            )
        await self.call(
            self.collection("messages").create_index,
            [("session_id", ASCENDING), ("created_at", ASCENDING)],
        )

    @staticmethod
    def clean(record):
        if record is not None:
            record.pop("_id", None)
        return record

    async def get(self, collection, identifier):
        return self.clean(await self.call(self.collection(collection).find_one, {"id": identifier}))

    async def find(self, collection, query=None, *, limit=100, offset=0, sort=None):
        def operation():
            cursor = self.collection(collection).find(query or {}, {"_id": 0})
            if sort:
                cursor = cursor.sort(sort)
            return list(cursor.skip(offset).limit(limit))

        return await self.call(operation)

    async def put(self, collection, record):
        await self.call(
            self.collection(collection).replace_one, {"id": record["id"]}, record, upsert=True
        )

    async def patch(self, collection, identifier, fields, *, condition=None):
        """通过单文档条件更新切换活动版本，避免把删除中的资料重新激活。"""
        query = {"id": identifier, **(condition or {})}
        return self.clean(
            await self.call(
                self.collection(collection).find_one_and_update,
                query,
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        )

    async def update_many(self, collection, query, fields):
        return await self.call(self.collection(collection).update_many, query, {"$set": fields})

    async def delete(self, collection, query):
        return await self.call(self.collection(collection).delete_many, query)

    async def count(self, collection, query):
        return await self.call(self.collection(collection).count_documents, query)
