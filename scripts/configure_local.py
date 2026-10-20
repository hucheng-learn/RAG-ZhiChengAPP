"""为独立 Compose 生成凭据并更新基础服务地址，保留现有 LLM 和模型配置。"""

import secrets
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values, set_key


def main():
    """幂等生成密钥；不读取旧容器配置，不打印密码，不覆盖已生成的部署密钥。"""
    target = Path(".env")
    if not target.exists():
        target.write_text("# 知成本地配置；禁止提交\n", encoding="utf-8")
    values = dotenv_values(target)
    initialized = values.get("ZHICHENGAPP_DEPLOYMENT_CONFIGURED") == "1"
    defaults = {
        "ZHICHENGAPP_MONGO_USER": "zhichengapp_admin",
        "ZHICHENGAPP_MINIO_ACCESS_KEY": "zhichengapp_admin",
    }
    for key in (
        "ZHICHENGAPP_MONGO_PASSWORD",
        "ZHICHENGAPP_MINIO_SECRET_KEY",
        "ZHICHENGAPP_MILVUS_PASSWORD",
    ):
        defaults[key] = secrets.token_urlsafe(24)
    for key, default in defaults.items():
        if not values.get(key) or (not initialized and key.startswith("ZHICHENGAPP_MINIO_")):
            values[key] = default
            set_key(str(target), key, default)
    password = quote(values["ZHICHENGAPP_MONGO_PASSWORD"], safe="")
    updates = {
        "ZHICHENGAPP_MONGO_URI": f"mongodb://{values['ZHICHENGAPP_MONGO_USER']}:{password}@127.0.0.1:27018/?authSource=admin",
        "ZHICHENGAPP_MILVUS_URI": "http://127.0.0.1:19531",
        "ZHICHENGAPP_MILVUS_TOKEN": "root:" + values["ZHICHENGAPP_MILVUS_PASSWORD"],
        "ZHICHENGAPP_MINIO_ENDPOINT": "127.0.0.1:9002",
        "ZHICHENGAPP_MINIO_SECURE": "false",
        "ZHICHENGAPP_LOAD_LOCAL_MODELS": "true",
        "ZHICHENGAPP_MODEL_DEVICE": values.get("ZHICHENGAPP_MODEL_DEVICE") or "cuda:0",
        "ZHICHENGAPP_DEPLOYMENT_CONFIGURED": "1",
    }
    for key, value in updates.items():
        set_key(str(target), key, value)
    print("Local deployment configured. Credentials are stored only in .env.")


if __name__ == "__main__":
    main()
