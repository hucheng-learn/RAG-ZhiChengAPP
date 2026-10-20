"""只读基础服务检查：uv run python -m scripts.doctor。"""

import asyncio
import json
import sys

from common.config import Settings
from utils.clients.infrastructure import Infrastructure


async def main() -> int:
    infrastructure = Infrastructure(Settings())
    try:
        await infrastructure.start()
        result = await infrastructure.readiness()
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
        return 0 if result.status == "ready" else 1
    finally:
        await infrastructure.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
