"""只读对比 dense / hybrid / rerank；不启动任务消费者，不改写在线任务状态。"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from common.config import Settings
from repositories.mongo import MongoRepository
from utils.clients.infrastructure import Infrastructure
from utils.clients.vector_store import VectorStore
from utils.llm.models import LocalModels


async def active_chunks(repo, hits):
    documents = {
        row["id"]: row
        for row in await repo.find(
            "documents",
            {"id": {"$in": list({hit["document_id"] for hit in hits})}},
            limit=500,
        )
    }
    output = []
    for hit in hits:
        document = documents.get(hit["document_id"])
        if (
            document
            and document.get("status") == "ready"
            and document.get("active_version") == hit["version_id"]
        ):
            chunk = await repo.get("chunks", hit["id"])
            if chunk:
                output.append(chunk)
    return output


def score(chunks, expected):
    """按人工标注章节计算 Recall@5；一章节多个块不重复计为多个证据。"""
    if not expected:
        return None
    return sum(
        any(
            chunk["source_name"] == source["source_name"]
            and chunk["title_path"] == source["title_path"]
            for chunk in chunks[:5]
        )
        for source in expected
    ) / len(expected)


async def benchmark(args):
    settings = Settings()
    infra = Infrastructure(settings)
    models = LocalModels(settings, infra.pool)
    results = []
    try:
        await infra.start()
        await models.start()
        models.require()
        if infra.mongo is None or infra.milvus is None:
            raise ValueError("请先配置基础服务")
        repo = MongoRepository(infra.mongo, settings.mongo_database, infra.pool)
        vectors = VectorStore(infra.milvus, settings.milvus_collection, infra.pool)
        cases = [
            json.loads(line)
            for line in args.cases.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for case in cases:
            if not case.get("reviewed") and not args.include_unreviewed:
                continue
            started = time.perf_counter()
            dense, sparse = await models.encode([case["question"]])
            filters = {"course_id": case["course_ids"], "content_type": "lecture"}
            baseline = await active_chunks(
                repo, await vectors.search(dense[0], sparse[0], filters, 20, mode="dense")
            )
            hybrid = await active_chunks(
                repo, await vectors.search(dense[0], sparse[0], filters, 30)
            )
            scores = (
                await models.rerank(case["question"], [row["embedding_text"] for row in hybrid])
                if hybrid
                else []
            )
            reranked = [
                row
                for _, row in sorted(zip(scores, hybrid, strict=True), key=lambda pair: -pair[0])
            ]
            expected = case.get("expected_sources", [])
            results.append(
                {
                    "id": case["id"],
                    "reviewed": case.get("reviewed", False),
                    "dense_section_recall_at_5": score(baseline, expected),
                    "hybrid_section_recall_at_5": score(hybrid, expected),
                    "rerank_section_recall_at_5": score(reranked, expected),
                    "seconds": time.perf_counter() - started,
                }
            )
            print(case["id"], "benchmarked")
        if not results:
            raise ValueError("没有已审核用例；冒烟运行可加 --include-unreviewed")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"granularity": "section", "results": results}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
    finally:
        await models.close()
        await infra.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/retrieval-benchmark.json"))
    parser.add_argument("--include-unreviewed", action="store_true")
    try:
        asyncio.run(benchmark(parser.parse_args()))
    except Exception as error:
        print(f"Benchmark failed: {type(error).__name__}")
        raise SystemExit(1) from None
