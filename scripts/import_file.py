"""通过正式 API 导入资料并等待任务，复用与页面完全相同的校验流程。"""

import argparse
import sys
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument(
        "--type", choices=["lecture", "project", "catalog", "question_bank"], default="lecture"
    )
    parser.add_argument("--course")
    parser.add_argument("--project")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=60) as client, args.path.open("rb") as source:
        data = {"content_type": args.type}
        if args.course:
            data["course_id"] = args.course
        if args.project:
            data["project_id"] = args.project
        response = client.post(
            "/api/v1/documents/import", data=data, files={"file": (args.path.name, source)}
        )
        response.raise_for_status()
        task = response.json()
        deadline = time.monotonic() + args.timeout
        previous = None
        while task["status"] in {"queued", "processing"} and time.monotonic() < deadline:
            if (task["status"], task["progress"]) != previous:
                print(task["task_id"], task["status"], task["progress"], task["stage"])
                previous = task["status"], task["progress"]
            time.sleep(1)
            response = client.get(f"/api/v1/import-tasks/{task['task_id']}")
            response.raise_for_status()
            task = response.json()
        print("最终状态：", task["status"], task.get("error") or "")
        return 0 if task["status"] == "completed" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"导入未完成（{type(error).__name__}），请检查路径、API 和服务状态")
        raise SystemExit(1) from None
