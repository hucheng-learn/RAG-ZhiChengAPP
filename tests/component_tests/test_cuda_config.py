"""检查默认 CUDA 设备，以及 CUDA 不可用时明确报错的行为。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from common.config import Settings
from common.errors import BusinessError
from utils.llm.models import LocalModels


def test_default_device_is_cuda():
    assert Settings(_env_file=None).model_device == "cuda:0"


def test_cuda_unavailable_does_not_fall_back_to_cpu(monkeypatch):
    import FlagEmbedding
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    created = []
    monkeypatch.setattr(FlagEmbedding, "BGEM3FlagModel", lambda *a, **kw: created.append(kw))
    with ThreadPoolExecutor(max_workers=1) as executor:
        models = LocalModels(Settings(_env_file=None, load_local_models=True), executor)
        asyncio.run(models.start())
        assert "CUDA 不可用" in models.error
        assert not created
        with pytest.raises(BusinessError, match="CUDA 不可用"):
            models.require()
