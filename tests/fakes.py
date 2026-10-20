"""仅用于测试的替身；生产应用始终使用 MongoDB、MinIO、Milvus 和真实模型。"""

import asyncio
import json
import threading
from copy import deepcopy


class CharacterTokenizer:
    """按字符模拟 token 计数，验证切片边界；不代表真实模型 tokenizer。"""

    def encode(self, text, add_special_tokens=True):
        return list(text) + ([0, 1] if add_special_tokens else [])


class FakeModels:
    """提供固定向量和简单重排分数，并允许模拟向量生成失败。"""

    def __init__(self):
        self.error = ""
        self.dimension = 2
        self.tokenizer = CharacterTokenizer()
        self.fail_encode = False

    async def start(self):
        pass

    def require(self):
        pass

    async def encode(self, texts):
        if self.fail_encode:
            raise RuntimeError("secret-embedding-error")
        return [[1.0, 0.0] for _ in texts], [{1: 1.0} for _ in texts]

    async def rerank(self, question, texts):
        return [len(set(question) & set(text)) / max(1, len(set(question))) for text in texts]

    async def close(self):
        pass


class FakeLLM:
    """提供可控模型响应，可模拟失败、错误引用与阻塞流。"""

    def __init__(self):
        self.client = object()
        self.fail = False
        self.invalid_citation = False
        self.supported = True
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()

    async def complete(self, messages):
        self.entered.set()
        await asyncio.to_thread(self.gate.wait)
        if self.fail:
            raise RuntimeError("secret-api-key")
        if "改写" in messages[0][1]:
            return "Python 列表是什么"
        data = json.loads(messages[-1][1])
        return json.dumps(
            {
                "supported": self.supported,
                "answer": data["evidence"][0]["text"]
                + (" [99]" if self.invalid_citation else " [1]"),
                "citation_ids": [99] if self.invalid_citation else [1],
            }
        )

    async def close(self):
        self.gate.set()

    async def stream(self, messages):
        raw = await self.complete(messages)
        for offset in range(0, len(raw), 8):
            yield raw[offset : offset + 8]


class FakeObjects:
    """用内存字典模拟 MinIO 文件存储。"""

    def __init__(self):
        self.objects = {}

    async def initialize(self):
        pass

    async def put(self, key, data, content_type=None):
        self.objects[key] = data

    async def get(self, key):
        return self.objects[key]

    async def remove(self, key):
        self.objects.pop(key, None)


class FakeVectors:
    """用内存记录模拟向量库，保留查询过滤条件供断言检查。"""

    def __init__(self):
        self.rows = {}
        self.last_filters = None

    async def initialize(self, dimension):
        pass

    async def upsert(self, rows):
        self.rows.update({row["id"]: deepcopy(row) for row in rows})

    async def delete_version(self, document_id, version_id=None):
        self.rows = {
            key: row
            for key, row in self.rows.items()
            if not (
                row["document_id"] == document_id
                and (version_id is None or row["version_id"] == version_id)
            )
        }

    async def verify(self, document_id, version_id, expected):
        assert (
            sum(
                row["document_id"] == document_id and row["version_id"] == version_id
                for row in self.rows.values()
            )
            == expected
        )

    async def search(self, dense, sparse, filters, limit):
        self.last_filters = filters
        return [
            {"id": row["id"], "document_id": row["document_id"], "version_id": row["version_id"]}
            for row in self.rows.values()
            if all(
                value is None or row.get(key) in value
                if isinstance(value, list)
                else value is None or row.get(key) == value
                for key, value in filters.items()
            )
        ][:limit]
