"""Detect tech stack from manifest files."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_stack(root: Path) -> dict[str, list[str]]:
    stack: dict[str, list[str]] = {
        "languages": [],
        "python_packages": [],
        "node_packages": [],
        "go_modules": [],
        "rust_crates": [],
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

    return stack


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
