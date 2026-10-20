"""实际运行 Embedding 与 Reranker，核对设备、数值和 CUDA 显存占用。"""

import asyncio
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common.config import Settings
from utils.llm.models import LocalModels


def write_report(output, result):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    import torch

    settings = Settings(load_local_models=True)
    with ThreadPoolExecutor(max_workers=1) as executor:
        models = LocalModels(settings, executor)
        try:
            await models.start()
            models.require()
            dense, sparse = await models.encode(["Python 列表可以保存多个元素。"])
            scores = await models.rerank("Python 列表保存什么？", ["Python 列表可以保存多个元素。"])
            embedding_device = str(next(models.embedding.model.parameters()).device)
            reranker_device = str(next(models.reranker.model.parameters()).device)
            if settings.model_device.startswith("cuda"):
                assert embedding_device == reranker_device == settings.model_device
                torch.cuda.synchronize()
            assert len(dense[0]) == 1024 and sparse[0]
            assert all(math.isfinite(value) for value in dense[0] + scores)
            result = {
                "torch": torch.__version__,
                "cuda_runtime": torch.version.cuda,
                "cuda_available": torch.cuda.is_available(),
                "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else None,
                "embedding_device": embedding_device,
                "reranker_device": reranker_device,
                "embedding_dtype": str(next(models.embedding.model.parameters()).dtype),
                "reranker_dtype": str(next(models.reranker.model.parameters()).dtype),
                "dense_dimension": len(dense[0]),
                "sparse_nonzero": len(sparse[0]),
                "rerank_scores": scores,
                "allocated_gpu_mib": round(torch.cuda.memory_allocated() / 1024**2, 1)
                if torch.cuda.is_available()
                else 0,
            }
            output = Path("output/model-validation.json")
            await asyncio.to_thread(write_report, output, result)
            print(json.dumps(result, ensure_ascii=True, indent=2))
        finally:
            await models.close()


if __name__ == "__main__":
    asyncio.run(main())
