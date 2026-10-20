import argparse
import json
from pathlib import Path

from common.config import Settings
from utils.parsers.docx import parse_docx
from utils.parsers.markdown import parse_markdown


def main():
    """生成待人工审核的章节候选题，绝不自动给自己标记为人工标注完成。"""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--course", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/eval-candidates.jsonl"))
    args = parser.parse_args()
    if args.path.suffix.lower() == ".docx":
        parsed = parse_docx(
            args.path.read_bytes(),
            document_id="eval",
            version_id="source",
            heading_styles=Settings().docx_heading_styles,
        )
    else:
        parsed = parse_markdown(
            args.path.read_text(encoding="utf-8-sig"), document_id="eval", version_id="source"
        )
    sections = [s for s in parsed.sections if s.title and len(s.body.strip()) >= 80]
    stride = max(1, len(sections) // 50)
    cases = [
        {
            "id": f"section-{index + 1:03}",
            "reviewed": False,
            "course_ids": [args.course],
            "question": f"请依据课程讲义解释：{' / '.join(s.title_path)}。",
            "expected_sources": [{"source_name": args.path.name, "title_path": s.title_path}],
            "expected_answer_contains": [],
            "expect_insufficient": False,
        }
        for index, s in enumerate(sections[::stride][:50])
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8"
    )
    print(f"Generated {len(cases)} unreviewed candidates: {args.output}")


if __name__ == "__main__":
    main()
