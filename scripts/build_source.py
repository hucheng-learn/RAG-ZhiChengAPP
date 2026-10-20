"""按交付清单打包知成源码，不收集本地配置、缓存或业务数据。"""

import argparse
import os
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    "README.md",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".env.example",
    ".gitignore",
)
SOURCE_DIRS = {
    "api": {".py"},
    "common": {".py"},
    "deploy": {".yml", ".yaml"},
    "docs": {".md"},
    "evals": {".md", ".jsonl"},
    "page": {".html", ".css", ".js"},
    "processor": {".py"},
    "repositories": {".py"},
    "schemas": {".py"},
    "scripts": {".py"},
    "services": {".py"},
    "tests": {".py", ".md", ".js"},
    "utils": {".py"},
}
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def checked_path(root: Path, path: Path) -> Path:
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Refusing linked or external path: {path.relative_to(root)}")
    return path


def source_files(root: Path) -> list[Path]:
    """先校验完整清单再创建 ZIP；白名单目录中的意外文件必须由维护者确认。"""
    root = root.resolve()
    files = [checked_path(root, root / name) for name in ROOT_FILES]
    files.extend(
        checked_path(root, root / name)
        for name in ("LICENSE", "LICENSE.md", "NOTICE", "NOTICE.md")
        if (root / name).exists()
    )
    for name, extensions in SOURCE_DIRS.items():
        directory = checked_path(root, root / name)
        if not directory.is_dir():
            raise ValueError(f"Missing source directory: {name}")
        for base, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(child for child in dirs if child not in CACHE_DIRS)
            for child in dirs:
                checked_path(root, Path(base) / child)
            for child in sorted(names):
                path = checked_path(root, Path(base) / child)
                if path.suffix in {".pyc", ".pyo"}:
                    continue
                if path.suffix not in extensions:
                    raise ValueError(f"Unexpected source file: {path.relative_to(root)}")
                files.append(path)
    if any(not path.is_file() for path in files):
        raise ValueError("A required source file is missing")
    return sorted(files)


def build_archive(root: Path, target: Path) -> int:
    root = root.resolve()
    files = source_files(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    # x 模式保证即使并发执行，也不会覆盖已有交付包。
    with ZipFile(target, "x", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, (Path("ZhiChengApp") / path.relative_to(root)).as_posix())
    return len(files)


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist" / f"ZhiChengApp-source-{version}.zip"
    )
    args = parser.parse_args()
    count = build_archive(ROOT, args.output)
    print(f"Created {args.output.resolve()} ({count} files)")


if __name__ == "__main__":
    main()
