import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def read_sse(response):
    """以终止事件确认结果；遇到断流明确失败，不把暂存文本当最终答案。"""
    started, first_token, event, result = time.perf_counter(), None, None, None
    for line in response.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            if event == "token" and first_token is None:
                first_token = time.perf_counter() - started
            elif event == "done":
                result = data["result"]
            elif event == "error":
                raise RuntimeError(data.get("code", "stream_error"))
    if result is None:
        raise RuntimeError("stream_interrupted")
    return result, first_token


def main():
    parser = argparse.ArgumentParser(
        description="评估最终回答来源命中，不冒充人工正确率或 Recall@5"
    )
    parser.add_argument("cases", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--output", type=Path, default=Path("output/eval-report.json"))
    parser.add_argument("--include-unreviewed", action="store_true")
    args = parser.parse_args()
    cases = [
        json.loads(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cases = [case for case in cases if case.get("reviewed") or args.include_unreviewed]
    if not cases:
        raise SystemExit(
            "No reviewed cases; review labels or use --include-unreviewed for smoke runs."
        )
    results = []
    with httpx.Client(base_url=args.url, timeout=600) as client:
        for case in cases:
            record = {"id": case["id"], "reviewed": case.get("reviewed", False)}
            session_id = None
            try:
                session = client.post(
                    "/api/v1/sessions", json={"course_ids": case.get("course_ids", [])}
                )
                session.raise_for_status()
                session_id = session.json()["session_id"]
                started = time.perf_counter()
                with client.stream(
                    "POST",
                    "/api/v1/query/stream",
                    json={
                        "session_id": session_id,
                        "question": case["question"],
                        "course_ids": case.get("course_ids", []),
                    },
                ) as response:
                    response.raise_for_status()
                    answer, first_token = read_sse(response)
                citations = answer.get("citations", [])
                expected = case.get("expected_sources", [])
                matched = [
                    any(
                        item.get("source_name") == source["source_name"]
                        and item.get("title_path") == source.get("title_path")
                        for item in citations
                    )
                    for source in expected
                ]
                record.update(
                    status=answer["status"],
                    total_seconds=time.perf_counter() - started,
                    first_display_chunk_seconds=first_token,
                    source_hit_ratio=sum(matched) / len(matched) if matched else None,
                    expected_text_match=all(
                        term in answer["answer"]
                        for term in case.get("expected_answer_contains", [])
                    )
                    if case.get("expected_answer_contains")
                    else None,
                    insufficiency_match=(answer["status"] == "insufficient_evidence")
                    if case.get("expect_insufficient")
                    else None,
                    answer=answer,
                )
            except Exception as error:
                record["error"] = type(error).__name__
            finally:
                if session_id:
                    try:
                        client.delete(f"/api/v1/sessions/{session_id}")
                    except httpx.HTTPError:
                        record["cleanup_error"] = "session_cleanup_failed"
            results.append(record)
            print(case["id"], record.get("status", record.get("error")))
    latencies = [r["total_seconds"] for r in results if "total_seconds" in r]
    report = {
        "cases": len(results),
        "reviewed_cases": sum(r["reviewed"] for r in results),
        "successful_requests": len(latencies),
        "mean_total_seconds": statistics.mean(latencies) if latencies else None,
        "human_answer_accuracy": None,
        "human_citation_support_rate": None,
        "retrieval_recall_at_5": None,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
