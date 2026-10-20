import asyncio
import json
from functools import partial

from pymilvus import AnnSearchRequest, DataType, RRFRanker


class VectorStore:
    def __init__(self, client, collection, executor, timeout=10):
        self.client, self.collection, self.executor, self.timeout = (
            client,
            collection,
            executor,
            timeout,
        )

    async def call(self, operation, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(operation, *args, **kwargs)
        )

    async def initialize(self, dimension: int):
        """创建稠密/学习型稀疏索引，并校验已存在集合的模型维度。"""
        if await self.call(self.client.has_collection, self.collection, timeout=self.timeout):
            schema = await self.call(
                self.client.describe_collection, self.collection, timeout=self.timeout
            )
            fields = {field["name"]: field for field in schema["fields"]}
            if not {
                "id",
                "document_id",
                "version_id",
                "course_id",
                "project_id",
                "content_type",
                "question_type",
                "dense",
                "sparse",
            }.issubset(fields):
                raise ValueError("向量集合 schema 不兼容")
            if int(fields["dense"]["params"]["dim"]) != dimension:
                raise ValueError("Embedding 维度与现有集合不兼容")
        else:
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=200)
            for name in (
                "document_id",
                "version_id",
                "course_id",
                "project_id",
                "content_type",
                "question_type",
            ):
                schema.add_field(name, DataType.VARCHAR, max_length=200)
            schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dimension)
            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            indexes = self.client.prepare_index_params()
            indexes.add_index(
                "dense",
                index_type="HNSW",
                metric_type="IP",
                params={"M": 16, "efConstruction": 128},
            )
            indexes.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
            await self.call(
                self.client.create_collection,
                self.collection,
                schema=schema,
                index_params=indexes,
                consistency_level="Strong",
                timeout=self.timeout,
            )
        await self.call(self.client.load_collection, self.collection, timeout=self.timeout)

    async def upsert(self, rows):
        if rows:
            await self.call(self.client.upsert, self.collection, rows, timeout=self.timeout)

    async def delete_version(self, document_id, version_id=None):
        """用 JSON 编码构造过滤字面量，禁止将用户文本直接拼成 Milvus 表达式。"""
        if not await self.call(self.client.has_collection, self.collection, timeout=self.timeout):
            return
        expression = f"document_id == {json.dumps(document_id)}"
        if version_id:
            expression += f" and version_id == {json.dumps(version_id)}"
        await self.call(
            self.client.delete, self.collection, filter=expression, timeout=self.timeout
        )

    async def verify(self, document_id, version_id, expected):
        expression = (
            f"document_id == {json.dumps(document_id)} and version_id == {json.dumps(version_id)}"
        )
        rows = await self.call(
            self.client.query,
            self.collection,
            filter=expression,
            output_fields=["count(*)"],
            consistency_level="Strong",
            timeout=self.timeout,
        )
        if not rows or rows[0]["count(*)"] != expected:
            raise ValueError("新版本索引数量校验失败")

    async def search(self, dense, sparse, filters, limit, *, mode="hybrid"):
        """两路召回只融合一次；过滤先于 ANN，活动版本仍须在服务层核对。"""
        clauses = []
        for key, value in filters.items():
            if key not in {"course_id", "project_id", "content_type", "question_type"}:
                raise ValueError("未知检索过滤字段")
            if isinstance(value, list):
                clauses.append(f"{key} in {json.dumps(value)}")
            elif value is not None:
                clauses.append(f"{key} == {json.dumps(value)}")
        expression = " and ".join(clauses)
        if mode == "dense":
            result = await self.call(
                self.client.search,
                self.collection,
                [dense],
                anns_field="dense",
                filter=expression,
                limit=limit,
                search_params={"metric_type": "IP", "params": {"ef": max(64, limit)}},
                output_fields=["document_id", "version_id"],
                consistency_level="Strong",
                timeout=self.timeout,
            )
            return [{"id": hit["id"], **hit["entity"]} for hit in result[0]]
        if mode != "hybrid":
            raise ValueError("未知检索策略")
        requests = [
            AnnSearchRequest(
                [dense],
                "dense",
                {"metric_type": "IP", "params": {"ef": max(64, limit)}},
                limit=limit,
                expr=expression,
            )
        ]
        if sparse:
            requests.append(
                AnnSearchRequest(
                    [sparse], "sparse", {"metric_type": "IP"}, limit=limit, expr=expression
                )
            )
        result = await self.call(
            self.client.hybrid_search,
            self.collection,
            requests,
            RRFRanker(),
            limit=limit,
            output_fields=["document_id", "version_id"],
            consistency_level="Strong",
            timeout=self.timeout,
        )
        return [{"id": hit["id"], **hit["entity"]} for hit in result[0]]
