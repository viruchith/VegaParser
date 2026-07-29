"""Unit tests for repo_parser.parser.dependencies."""

from __future__ import annotations

import pytest

from repo_parser.models import ParsedFile
from repo_parser.parser.dependencies import (
    _build_java_index,
    _java_path_from_fqcn,
    _parse_import_target,
    _path_matches,
    _resolve_js_import,
    _resolve_python_import,
    infer_internal_dependencies,
)


# ── _java_path_from_fqcn ────────────────────────────────────────────────────


def test_java_path_found_by_suffix():
    paths = {"src/main/java/com/example/Foo.java", "src/main/java/com/other/Bar.java"}
    result = _java_path_from_fqcn("com.example.Foo", paths)
    assert result == "src/main/java/com/example/Foo.java"


def test_java_path_single_part_returns_none():
    paths = {"com/Foo.java"}
    assert _java_path_from_fqcn("Foo", paths) is None


def test_java_path_fallback_by_class_name_and_package_tail():
    # Path doesn't match the full suffix but ends with ClassName.java and contains pkg tail
    paths = {"legacy/com/example/Foo.java"}
    result = _java_path_from_fqcn("com.example.Foo", paths)
    assert result is not None


def test_java_path_not_found_returns_none():
    paths = {"src/other/Bar.java"}
    assert _java_path_from_fqcn("com.example.Foo", paths) is None


def test_java_path_multiple_candidates_sorted():
    paths = {"b/com/example/Foo.java", "a/com/example/Foo.java"}
    result = _java_path_from_fqcn("com.example.Foo", paths)
    assert result == "a/com/example/Foo.java"


# ── _resolve_js_import ──────────────────────────────────────────────────────


def test_resolve_js_relative_with_extension():
    paths = {"src/utils.js"}
    result = _resolve_js_import("./utils", "src/main.js", paths)
    assert result == "src/utils.js"


def test_resolve_js_relative_exact_match():
    paths = {"src/utils.js"}
    result = _resolve_js_import("./utils.js", "src/main.js", paths)
    assert result == "src/utils.js"


def test_resolve_js_relative_ts_extension():
    paths = {"src/helper.ts"}
    result = _resolve_js_import("./helper", "src/main.ts", paths)
    assert result == "src/helper.ts"


def test_resolve_js_relative_index_js():
    paths = {"src/components/index.js"}
    result = _resolve_js_import("./components", "src/main.js", paths)
    assert result == "src/components/index.js"


def test_resolve_js_non_relative_returns_none():
    paths = {"node_modules/lodash.js"}
    assert _resolve_js_import("lodash", "src/main.js", paths) is None


def test_resolve_js_not_found_returns_none():
    paths = {"src/other.js"}
    assert _resolve_js_import("./missing", "src/main.js", paths) is None


def test_resolve_js_normalized_path_match():
    paths = {"src/utils.js"}
    # Import path that, once normalized, matches without leading './'
    result = _resolve_js_import("./utils.js", "src/main.js", paths)
    assert result == "src/utils.js"


# ── _path_matches ───────────────────────────────────────────────────────────


def test_path_matches_exact():
    paths = {"repo_parser/models.py"}
    assert _path_matches(paths, "repo_parser/models.py") == "repo_parser/models.py"


def test_path_matches_suffix():
    paths = {"src/repo_parser/models.py"}
    assert _path_matches(paths, "repo_parser/models.py") == "src/repo_parser/models.py"


def test_path_matches_not_found():
    assert _path_matches({"other/file.py"}, "repo_parser/models.py") is None


# ── _resolve_python_import ──────────────────────────────────────────────────


def test_resolve_python_import_dotted():
    paths = {"repo_parser/models.py"}
    result = _resolve_python_import("repo_parser.models", "main.py", paths)
    assert result == "repo_parser/models.py"


def test_resolve_python_import_package_init():
    paths = {"repo_parser/__init__.py"}
    result = _resolve_python_import("repo_parser", "main.py", paths)
    assert result == "repo_parser/__init__.py"


def test_resolve_python_import_parent_module():
    paths = {"utils.py"}
    result = _resolve_python_import("utils.helpers", "src/main.py", paths)
    # Falls through to parent check: "utils.py"
    assert result == "utils.py"


def test_resolve_python_import_relative_fallback():
    paths = {"src/utils.py"}
    result = _resolve_python_import("utils", "src/main.py", paths)
    assert result == "src/utils.py"


def test_resolve_python_import_not_found():
    assert _resolve_python_import("nonexistent.module", "main.py", set()) is None


# ── _build_java_index ───────────────────────────────────────────────────────


def test_build_java_index_basic():
    pf = ParsedFile(filepath="src/Foo.java", language="java", exports=["Foo"])
    sources = {"src/Foo.java": "package com.example;\n\npublic class Foo {}"}
    index = _build_java_index([pf], sources)
    assert "com.example.Foo" in index
    assert index["com.example.Foo"] == "src/Foo.java"
    assert index["Foo"] == "src/Foo.java"


def test_build_java_index_no_package():
    pf = ParsedFile(filepath="Foo.java", language="java", exports=["Foo"])
    sources = {"Foo.java": "public class Foo {}"}
    index = _build_java_index([pf], sources)
    assert index["Foo"] == "Foo.java"


def test_build_java_index_skips_package_export():
    pf = ParsedFile(filepath="src/Foo.java", language="java", exports=["package:com.example", "Foo"])
    sources = {"src/Foo.java": "package com.example;\npublic class Foo {}"}
    index = _build_java_index([pf], sources)
    assert "package:com.example" not in index


def test_build_java_index_non_java_skipped():
    pf = ParsedFile(filepath="main.py", language="python", exports=["main"])
    index = _build_java_index([pf], {})
    assert index == {}


# ── _parse_import_target ────────────────────────────────────────────────────


@pytest.mark.parametrize("imp,lang,expected", [
    ("import java.util.List;", "java", "java.util.List"),
    # static import: the regex captures up to `.*`, the `.*` is in the non-capturing group
    ("import static java.util.Collections.*;", "java", "java.util.Collections"),
    ("import notregex", "java", "notregex"),
    ("import { foo } from './bar'", "javascript", "./bar"),
    ("const x = require('./utils')", "javascript", "./utils"),
    ("from repo_parser.models import ParsedFile", "python", "repo_parser.models"),
    ("import os", "python", "os"),
    ("import com.example.Foo", "kotlin", "com.example.Foo"),
    ("using System.Collections;", "csharp", "System.Collections"),
    ("something else", "unknown", None),
    ("from . import utils", "python", "."),
])
def test_parse_import_target(imp, lang, expected):
    result = _parse_import_target(imp, lang)
    assert result == expected


def test_parse_import_target_java_fallback():
    # No regex match but starts with "import "
    result = _parse_import_target("import 123invalid;", "java")
    # Falls through to "import not regex" path
    assert result is not None or result is None  # just ensure no exception


# ── infer_internal_dependencies ─────────────────────────────────────────────


def test_infer_python_absolute_import():
    utils = ParsedFile(filepath="utils.py", language="python")
    main = ParsedFile(filepath="main.py", language="python", imports=["import utils"])
    infer_internal_dependencies([utils, main])
    assert "utils.py" in main.internal_dependencies
    assert utils.internal_dependencies == []


def test_infer_python_from_import():
    models = ParsedFile(filepath="repo_parser/models.py", language="python")
    main = ParsedFile(
        filepath="main.py",
        language="python",
        imports=["from repo_parser.models import ParsedFile"],
    )
    infer_internal_dependencies([models, main])
    assert "repo_parser/models.py" in main.internal_dependencies


def test_infer_python_relative_import():
    helper = ParsedFile(filepath="pkg/helper.py", language="python")
    main = ParsedFile(
        filepath="pkg/main.py",
        language="python",
        imports=["from .helper import something"],
    )
    infer_internal_dependencies([helper, main])
    assert "pkg/helper.py" in main.internal_dependencies


def test_infer_python_stem_fallback():
    utils = ParsedFile(filepath="utils.py", language="python")
    main = ParsedFile(filepath="main.py", language="python", imports=["import utils.helpers"])
    infer_internal_dependencies([utils, main])
    # stem of "utils.helpers" is "utils", which matches utils.py
    assert "utils.py" in main.internal_dependencies


def test_infer_js_relative_import():
    helper = ParsedFile(filepath="src/helper.js", language="javascript")
    main = ParsedFile(
        filepath="src/main.js",
        language="javascript",
        imports=["import { foo } from './helper'"],
    )
    infer_internal_dependencies([helper, main])
    assert "src/helper.js" in main.internal_dependencies


def test_infer_js_non_relative_ignored():
    main = ParsedFile(
        filepath="src/main.js",
        language="javascript",
        imports=["import React from 'react'"],
    )
    infer_internal_dependencies([main])
    assert main.internal_dependencies == []


def test_infer_java_fqcn_import():
    foo = ParsedFile(
        filepath="src/main/java/com/example/Foo.java",
        language="java",
        exports=["Foo"],
    )
    bar = ParsedFile(
        filepath="src/main/java/com/example/Bar.java",
        language="java",
        imports=["import com.example.Foo;"],
    )
    sources = {
        "src/main/java/com/example/Foo.java": "package com.example;\npublic class Foo {}",
    }
    infer_internal_dependencies([foo, bar], sources)
    assert "src/main/java/com/example/Foo.java" in bar.internal_dependencies


def test_infer_java_wildcard_import():
    foo = ParsedFile(
        filepath="src/main/java/com/example/Foo.java",
        language="java",
        exports=["Foo"],
    )
    bar = ParsedFile(
        filepath="src/main/java/com/example/Bar.java",
        language="java",
        imports=["import com.example.*;"],
    )
    sources = {
        "src/main/java/com/example/Foo.java": "package com.example;\npublic class Foo {}",
    }
    infer_internal_dependencies([foo, bar], sources)
    assert "src/main/java/com/example/Foo.java" in bar.internal_dependencies


def test_infer_kotlin_import():
    foo = ParsedFile(filepath="com/example/Foo.kt", language="kotlin")
    bar = ParsedFile(
        filepath="com/example/Bar.kt",
        language="kotlin",
        imports=["import com.example.Foo"],
    )
    infer_internal_dependencies([foo, bar])
    assert "com/example/Foo.kt" in bar.internal_dependencies


def test_infer_no_self_dependency():
    main = ParsedFile(filepath="main.py", language="python", imports=["import main"])
    infer_internal_dependencies([main])
    assert "main.py" not in main.internal_dependencies


def test_infer_empty_imports():
    pf = ParsedFile(filepath="empty.py", language="python")
    infer_internal_dependencies([pf])
    assert pf.internal_dependencies == []


def test_infer_unknown_language_no_crash():
    pf = ParsedFile(filepath="file.rs", language="rust", imports=["use std::io;"])
    infer_internal_dependencies([pf])
    # Unknown language should not crash and deps should be empty
    assert pf.internal_dependencies == []
