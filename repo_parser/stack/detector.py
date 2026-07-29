"""Detect tech stack from manifest files."""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SPRING_KEYWORDS = frozenset({
    "spring-boot", "spring-boot-starter", "spring-core", "spring-web",
    "spring-data", "spring-security", "spring-cloud", "spring-kafka",
    "spring-batch", "spring-context", "spring-beans",
})


def detect_stack(root: Path) -> dict[str, list[str]]:
    stack: dict[str, list[str]] = {
        "languages": [],
        "python_packages": [],
        "node_packages": [],
        "go_modules": [],
        "rust_crates": [],
        "java_packages": [],
        "other": [],
    }

    req = root / "requirements.txt"
    if req.is_file():
        stack["python_packages"] = _parse_requirements(req)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        stack["other"].append("pyproject.toml detected")

    pkg = root / "package.json"
    if pkg.is_file():
        stack["node_packages"] = _parse_package_json(pkg)

    go_mod = root / "go.mod"
    if go_mod.is_file():
        stack["go_modules"] = _parse_go_mod(go_mod)

    cargo = root / "Cargo.toml"
    if cargo.is_file():
        stack["rust_crates"] = _parse_cargo_toml(cargo)

    java_deps = _parse_maven_pom(root) + _parse_gradle_build(root)
    if java_deps:
        stack["java_packages"] = _dedupe_prioritize_spring(java_deps)

    return stack


def _dedupe_prioritize_spring(deps: list[str]) -> list[str]:
    seen: set[str] = set()
    spring: list[str] = []
    other: list[str] = []
    for dep in deps:
        if dep in seen:
            continue
        seen.add(dep)
        if any(kw in dep.lower() for kw in SPRING_KEYWORDS):
            spring.append(dep)
        else:
            other.append(dep)
    return spring[:40] + other[:40]


def _parse_maven_pom(root: Path) -> list[str]:
    """Parse pom.xml from root and common Maven module paths."""
    deps: list[str] = []
    pom_paths = [root / "pom.xml"]
    pom_paths.extend(root.glob("*/pom.xml"))
    pom_paths.extend(root.glob("**/pom.xml"))
    seen_paths: set[str] = set()

    for pom in pom_paths[:10]:
        path_key = str(pom.resolve())
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        deps.extend(_parse_single_pom(pom))

    return deps


def _parse_single_pom(path: Path) -> list[str]:
    packages: list[str] = []
    try:
        tree = ET.parse(path)
        root_el = tree.getroot()
        ns = ""
        if root_el.tag.startswith("{"):
            ns = root_el.tag.split("}")[0] + "}"

        for dep in root_el.iter(f"{ns}dependency"):
            group = dep.find(f"{ns}groupId")
            artifact = dep.find(f"{ns}artifactId")
            if group is not None and artifact is not None and group.text and artifact.text:
                coord = f"{group.text}:{artifact.text}"
                packages.append(coord)

        # Spring Boot parent
        parent = root_el.find(f"{ns}parent")
        if parent is not None:
            pg = parent.find(f"{ns}groupId")
            pa = parent.find(f"{ns}artifactId")
            if pg is not None and pa is not None and pg.text and pa.text:
                packages.append(f"{pg.text}:{pa.text} (parent)")
    except (ET.ParseError, OSError) as exc:
        logger.warning("Could not parse %s: %s", path, exc)
    return packages


def _parse_gradle_build(root: Path) -> list[str]:
    """Parse build.gradle / build.gradle.kts for dependencies."""
    deps: list[str] = []
    gradle_files = [
        root / "build.gradle",
        root / "build.gradle.kts",
        root / "settings.gradle",
    ]
    gradle_files.extend(root.glob("**/build.gradle"))
    gradle_files.extend(root.glob("**/build.gradle.kts"))

    dep_pattern = re.compile(
        r"""(?:implementation|api|compile|runtimeOnly|testImplementation)\s*[\(\s]['"]([^'"]+)['"]""",
    )
    coord_pattern = re.compile(
        r"""['"]([a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+(?::[a-zA-Z0-9_.-]+)?)['"]""",
    )

    seen_files: set[str] = set()
    for gf in gradle_files[:10]:
        key = str(gf.resolve())
        if key in seen_files or not gf.is_file():
            continue
        seen_files.add(key)
        try:
            text = gf.read_text(encoding="utf-8", errors="replace")
            for match in dep_pattern.finditer(text):
                deps.append(match.group(1))
            for match in coord_pattern.finditer(text):
                coord = match.group(1)
                if ":" in coord and coord not in deps:
                    deps.append(coord)
        except OSError as exc:
            logger.warning("Could not read %s: %s", gf, exc)
    return deps


def _parse_requirements(path: Path) -> list[str]:
    packages = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            name = line.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
            if name:
                packages.append(name)
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
    return packages


def _parse_package_json(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        deps = list(data.get("dependencies", {}).keys())
        deps.extend(data.get("devDependencies", {}).keys())
        return sorted(set(deps))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not parse %s: %s", path, exc)
        return []


def _parse_go_mod(path: Path) -> list[str]:
    modules = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("require "):
                mod = line.replace("require ", "").split()[0]
                modules.append(mod)
            elif line.startswith("\t") and " " in line and not line.startswith("//"):
                mod = line.strip().split()[0]
                if mod and not mod.startswith("//"):
                    modules.append(mod)
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
    return modules[:50]


def _parse_cargo_toml(path: Path) -> list[str]:
    crates = []
    in_deps = False
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped == "[dependencies]":
                in_deps = True
                continue
            if stripped.startswith("[") and stripped != "[dependencies]":
                in_deps = False
                continue
            if in_deps and "=" in stripped and not stripped.startswith("#"):
                name = stripped.split("=")[0].strip()
                crates.append(name)
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
    return crates
