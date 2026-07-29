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


# ── Maven POM parsing ────────────────────────────────────────────────────────


def test_detects_maven_pom_dependencies(tmp_path):
    pom = """\
<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
    </dependency>
  </dependencies>
</project>
"""
    (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    stack = detect_stack(tmp_path)
    assert any("spring-boot-starter-web" in p for p in stack["java_packages"])
    assert any("jackson-databind" in p for p in stack["java_packages"])


def test_spring_dependencies_prioritized(tmp_path):
    pom = """\
<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter</artifactId>
    </dependency>
  </dependencies>
</project>
"""
    (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    stack = detect_stack(tmp_path)
    pkgs = stack["java_packages"]
    spring_idx = next((i for i, p in enumerate(pkgs) if "spring" in p.lower()), None)
    jackson_idx = next((i for i, p in enumerate(pkgs) if "jackson" in p.lower()), None)
    assert spring_idx is not None and jackson_idx is not None
    assert spring_idx < jackson_idx


def test_maven_pom_with_parent(tmp_path):
    pom = """\
<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
  </parent>
</project>
"""
    (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    stack = detect_stack(tmp_path)
    assert any("spring-boot-starter-parent" in p for p in stack["java_packages"])


def test_maven_pom_corrupt_xml(tmp_path):
    (tmp_path / "pom.xml").write_text("NOT XML", encoding="utf-8")
    # Should not raise
    stack = detect_stack(tmp_path)
    assert stack["java_packages"] == []


# ── Gradle build parsing ─────────────────────────────────────────────────────


def test_detects_gradle_dependencies(tmp_path):
    gradle = """\
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web:3.0.0'
    testImplementation 'org.junit.jupiter:junit-jupiter:5.9.0'
}
"""
    (tmp_path / "build.gradle").write_text(gradle, encoding="utf-8")
    stack = detect_stack(tmp_path)
    assert any("spring-boot-starter-web" in p for p in stack["java_packages"])


# ── pyproject.toml detection ─────────────────────────────────────────────────


def test_detects_pyproject_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'app'\n", encoding="utf-8")
    stack = detect_stack(tmp_path)
    assert any("pyproject.toml" in item for item in stack["other"])


# ── _dedupe_prioritize_spring ────────────────────────────────────────────────


def test_dedupe_prioritize_spring_deduplicates():
    from repo_parser.stack.detector import _dedupe_prioritize_spring
    deps = ["a:b", "a:b", "c:d"]
    result = _dedupe_prioritize_spring(deps)
    assert result.count("a:b") == 1


def test_dedupe_empty():
    from repo_parser.stack.detector import _dedupe_prioritize_spring
    assert _dedupe_prioritize_spring([]) == []
