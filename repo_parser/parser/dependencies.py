"""Resolve import statements to internal repository file paths."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from repo_parser.models import ParsedFile

logger = logging.getLogger(__name__)

JAVA_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?([a-zA-Z_][\w.]*)(?:\.\*)?\s*;",
)
JS_IMPORT_FROM_RE = re.compile(
    r"""import\s+(?:type\s+)?(?:[\w*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]""",
)
JS_REQUIRE_RE = re.compile(
    r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)
PYTHON_FROM_IMPORT_RE = re.compile(
    r"^\s*from\s+([\w.]+)\s+import",
)
PYTHON_IMPORT_RE = re.compile(
    r"^\s*import\s+([\w.]+)",
)
KOTLIN_IMPORT_RE = re.compile(
    r"^\s*import\s+([a-zA-Z_][\w.]*)",
)
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _java_path_from_fqcn(fqcn: str, module_paths: set[str]) -> str | None:
    """Map com.example.Foo -> src/.../com/example/Foo.java if it exists in the repo."""
    parts = fqcn.split(".")
    if len(parts) < 2:
        return None
    class_name = parts[-1]
    package_parts = parts[:-1]
    suffix_path = "/".join(package_parts + [f"{class_name}.java"])
    candidates = [p for p in module_paths if p.endswith(suffix_path) or p.replace("\\", "/").endswith(suffix_path)]
    if candidates:
        return sorted(candidates)[0]
    # Fallback: match by class file name under package tail
    for path in module_paths:
        if path.endswith(f"{class_name}.java") and all(part in path for part in package_parts[-2:]):
            return path
    return None


def _resolve_js_import(import_path: str, from_file: str, module_paths: set[str]) -> str | None:
    if not import_path.startswith("."):
        return None
    base = Path(from_file).parent
    raw = (base / import_path).as_posix()
    candidates = [
        raw,
        f"{raw}.js",
        f"{raw}.ts",
        f"{raw}.jsx",
        f"{raw}.tsx",
        f"{raw}/index.js",
        f"{raw}/index.ts",
    ]
    for c in candidates:
        if c in module_paths:
            return c
        normalized = c.lstrip("./")
        matches = [p for p in module_paths if p == normalized or p.endswith(f"/{normalized}")]
        if matches:
            return sorted(matches)[0]
    return None


def _path_matches(module_paths: set[str], candidate: str) -> str | None:
    """Match a repo-relative path candidate against parsed module paths."""
    if candidate in module_paths:
        return candidate
    suffix = f"/{candidate}"
    for p in module_paths:
        if p == candidate or p.endswith(suffix):
            return p
    return None


def _resolve_python_import(import_name: str, from_file: str, module_paths: set[str]) -> str | None:
    parts = import_name.split(".")
    # from . import x  handled elsewhere
    candidates = ["/".join(parts) + ".py", "/".join(parts) + "/__init__.py"]
    if len(parts) > 1:
        candidates.append("/".join(parts[:-1]) + ".py")
    for cand in candidates:
        resolved = _path_matches(module_paths, cand)
        if resolved:
            return resolved
    # Relative from package structure
    base = Path(from_file).parent
    rel = (base / parts[0]).with_suffix(".py").as_posix()
    if rel in module_paths:
        return rel
    return None


def _build_java_index(parsed_files: list[ParsedFile], sources: dict[str, str]) -> dict[str, str]:
    """Map fully-qualified Java class names to file paths."""
    index: dict[str, str] = {}
    for pf in parsed_files:
        if not pf.filepath.endswith(".java"):
            continue
        source = sources.get(pf.filepath, "")
        package_match = PACKAGE_RE.search(source)
        package = package_match.group(1) if package_match else ""
        for export in pf.exports:
            if export.startswith("package:"):
                continue
            fqcn = f"{package}.{export}" if package else export
            index[fqcn] = pf.filepath
            # Also index without outer packages for partial matches
            index[export] = pf.filepath
    return index


def _parse_import_target(imp: str, language: str) -> str | None:
    """Extract the import target string from a raw import statement."""
    imp = imp.strip()
    if language == "java":
        m = JAVA_IMPORT_RE.match(imp)
        if m:
            return m.group(1)
        if imp.startswith("import "):
            return imp.replace("import", "").replace("static", "").strip().rstrip(";").strip()
    if language in ("javascript", "typescript"):
        m = JS_IMPORT_FROM_RE.search(imp)
        if m:
            return m.group(1)
        m = JS_REQUIRE_RE.search(imp)
        if m:
            return m.group(1)
    if language == "python":
        m = PYTHON_FROM_IMPORT_RE.match(imp)
        if m:
            return m.group(1)
        m = PYTHON_IMPORT_RE.match(imp)
        if m:
            return m.group(1)
    if language == "kotlin":
        m = KOTLIN_IMPORT_RE.match(imp)
        if m:
            return m.group(1)
    if language == "csharp" and imp.startswith("using "):
        return imp.replace("using", "").strip().rstrip(";").strip()
    return None


def infer_internal_dependencies(
    parsed_files: list[ParsedFile],
    sources: dict[str, str] | None = None,
) -> None:
    """Link import statements to internal module paths across languages."""
    sources = sources or {}
    module_paths = {pf.filepath for pf in parsed_files}
    java_index = _build_java_index(parsed_files, sources)

    # Python stem map
    stem_map: dict[str, str] = {}
    for path in module_paths:
        p = Path(path)
        stem_map[p.stem] = path

    for pf in parsed_files:
        deps: set[str] = set()
        for imp in pf.imports:
            target = _parse_import_target(imp, pf.language)
            if not target:
                continue

            if pf.language == "java":
                resolved = _java_path_from_fqcn(target, module_paths)
                if not resolved and "." in target:
                    # import com.foo.Bar -> try full class
                    resolved = java_index.get(target)
                if not resolved:
                    # import com.foo.* partial — match any class in package
                    pkg_prefix = target.rstrip(".*")
                    for fqcn, fpath in java_index.items():
                        if fqcn.startswith(pkg_prefix + ".") or fqcn == pkg_prefix:
                            deps.add(fpath)
                    continue
                if resolved and resolved != pf.filepath:
                    deps.add(resolved)

            elif pf.language in ("javascript", "typescript"):
                if target.startswith("."):
                    resolved = _resolve_js_import(target, pf.filepath, module_paths)
                    if resolved:
                        deps.add(resolved)

            elif pf.language == "python":
                if target.startswith("."):
                    base = Path(pf.filepath).parent
                    parts = target.split(".")
                    rel = base
                    for part in parts:
                        if part == "":
                            continue
                        rel = rel / part
                    for cand in [rel.with_suffix(".py"), rel / "__init__.py"]:
                        c = cand.as_posix()
                        if c in module_paths:
                            deps.add(c)
                else:
                    resolved = _resolve_python_import(target, pf.filepath, module_paths)
                    if resolved:
                        deps.add(resolved)
                    elif target.split(".")[0] in stem_map:
                        deps.add(stem_map[target.split(".")[0]])

            elif pf.language == "kotlin":
                kotlin_suffix = "/".join(target.split(".")) + ".kt"
                for p in module_paths:
                    if p.replace("\\", "/").endswith(kotlin_suffix):
                        deps.add(p)
                        break

        pf.internal_dependencies = sorted(d for d in deps if d != pf.filepath)
        if deps:
            logger.debug("%s internal deps: %s", pf.filepath, deps)
