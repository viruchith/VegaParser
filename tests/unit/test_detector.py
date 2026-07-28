"""Unit tests for the tech-stack detector."""

from __future__ import annotations

import json

from repo_parser.stack.detector import detect_stack


def test_detects_python_packages(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "flask==2.0.0\nrequests>=2.0\n# a comment\n-e .\n", encoding="utf-8"
    )
    stack = detect_stack(tmp_path)
    assert "flask" in stack["python_packages"]
    assert "requests" in stack["python_packages"]


def test_detects_node_packages(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"express": "^4.0.0"},
                "devDependencies": {"jest": "^29.0.0"},
            }
        ),
        encoding="utf-8",
    )
    stack = detect_stack(tmp_path)
    assert "express" in stack["node_packages"]
    assert "jest" in stack["node_packages"]


def test_detects_go_modules(tmp_path):
    (tmp_path / "go.mod").write_text(
        "module example.com/app\n\ngo 1.21\n\nrequire github.com/gin-gonic/gin v1.9.0\n",
        encoding="utf-8",
    )
    stack = detect_stack(tmp_path)
    assert any("gin-gonic/gin" in mod for mod in stack["go_modules"])


def test_detects_rust_crates(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "app"\n\n[dependencies]\nserde = "1.0"\ntokio = "1.0"\n',
        encoding="utf-8",
    )
    stack = detect_stack(tmp_path)
    assert "serde" in stack["rust_crates"]
    assert "tokio" in stack["rust_crates"]


def test_returns_empty_for_missing_files(tmp_path):
    stack = detect_stack(tmp_path)
    assert stack["python_packages"] == []
    assert stack["node_packages"] == []
    assert stack["go_modules"] == []
    assert stack["rust_crates"] == []
