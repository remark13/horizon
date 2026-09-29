from pathlib import Path

import pytest

from scripts import build_sources_ui_handoff as builder


@pytest.mark.parametrize("content", [
    '[project]\nversion = "0.4.50"\n',
    "[project]\nversion = '0.4.50' # trusted version\n",
    '[tool.example]\nversion = "wrong"\n[project]\nname = "saia"\nversion = "0.4.50"\n[tool.other]\nversion = "also-wrong"\n',
])
def test_project_version_works_without_python_311_tomllib(tmp_path, content):
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    assert builder.project_version(path) == "0.4.50"


@pytest.mark.parametrize("content", [
    '[tool.example]\nversion = "not-project"\n',
    '[project]\nname = "saia"\n[tool.example]\nversion = "not-project"\n',
])
def test_project_version_rejects_a_version_from_another_section(tmp_path, content):
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="Missing"):
        builder.project_version(path)


def test_frozen_old_builder_refuses_new_runtime_before_overwriting_archive(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.4.50"\n', encoding="utf-8")
    target = tmp_path / "old.zip"
    target.write_bytes(b"preserve-the-existing-historical-archive")
    monkeypatch.setattr(builder, "ROOT", Path(tmp_path))
    monkeypatch.setattr(builder, "OUTPUT", target)
    with pytest.raises(ValueError, match="must not be overwritten"):
        builder.main()
    assert target.read_bytes() == b"preserve-the-existing-historical-archive"
