"""检查源码打包排除私密文件和缓存，并拒绝覆盖及外部目录链接。"""

import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.build_source import ROOT_FILES, SOURCE_DIRS, build_archive


@pytest.fixture
def source_tree(tmp_path):
    for name in ROOT_FILES:
        (tmp_path / name).write_text("source", encoding="utf-8")
    for name, extensions in SOURCE_DIRS.items():
        directory = tmp_path / name
        directory.mkdir()
        (directory / ("example" + sorted(extensions)[0])).write_text("source", encoding="utf-8")
    return tmp_path


def test_archive_excludes_private_files_and_caches_and_refuses_overwrite(source_tree):
    (source_tree / ".env").write_text("private-key", encoding="utf-8")
    for name in (".venv", "output", "dist", "api/__pycache__"):
        directory = source_tree / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "private.txt").write_text("private", encoding="utf-8")
    target = source_tree / "dist" / "source.zip"
    count = build_archive(source_tree, target)
    with ZipFile(target) as archive:
        names = archive.namelist()
        assert len(names) == count
        assert "ZhiChengApp/.env.example" in names
        assert all(Path(name).parts[0] == "ZhiChengApp" for name in names)
        assert not any("private" in name or name.endswith("/.env") for name in names)
        assert all(archive.read(name) == b"source" for name in names)
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        build_archive(source_tree, target)
    assert target.read_bytes() == original


def test_archive_refuses_unexpected_private_file_in_source_directory(source_tree):
    (source_tree / "docs" / ".env").write_text("private-key", encoding="utf-8")
    target = source_tree / "source.zip"
    with pytest.raises(ValueError, match="Unexpected source file"):
        build_archive(source_tree, target)
    assert not target.exists()


def test_archive_refuses_directory_link_to_external_files(source_tree, tmp_path_factory):
    external = tmp_path_factory.mktemp("external-source")
    (external / "private.md").write_text("private-key", encoding="utf-8")
    link = source_tree / "docs" / "linked"
    # Windows 创建符号链接可能需要管理员权限，使用同样可能被目录遍历跟随的 junction。
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(external), str(link))
    else:
        link.symlink_to(external, target_is_directory=True)
    target = source_tree / "source.zip"
    with pytest.raises(ValueError, match="Refusing linked or external path"):
        build_archive(source_tree, target)
    assert not target.exists()
