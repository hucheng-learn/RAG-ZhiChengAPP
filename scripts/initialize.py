"""初始化仅限 zhichengapp 的索引、对象桶和向量集合，不修改旧业务命名空间。"""

import asyncio
import sys

from common.config import Settings
from services.runtime import Runtime
from utils.clients.infrastructure import Infrastructure


async def main():
    settings = Settings()
    infrastructure = Infrastructure(settings)
    runtime = None
    try:
        await infrastructure.start()
        runtime = Runtime(settings, infrastructure)
        await runtime.start()
        runtime.require_models()
        print("知成业务资源与模型已就绪")
    finally:
        if runtime:
            await runtime.close()
        await infrastructure.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"初始化未完成（{type(error).__name__}），请检查服务与配置")
        raise SystemExit(1) from None
