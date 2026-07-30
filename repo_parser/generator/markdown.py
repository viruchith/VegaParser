"""Markdown knowledge base generator using Jinja2."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from repo_parser.models import ParsedFile
from repo_parser.stack.detector import detect_stack

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


_MAX_FILENAME_BYTES = 180  # Safe margin below the macOS 255-byte per-component limit


def sanitize_filename(filepath: str) -> str:
    """Convert a repo-relative path to a safe markdown filename."""
    name = filepath.replace("\\", "/")
    name = re.sub(r"[^a-zA-Z0-9_./-]", "_", name)
    name = name.replace("/", "_").replace(".", "_")
    stem = f"{name}.md"
    if len(stem) > _MAX_FILENAME_BYTES:
        # Truncate and append a short hash so filenames stay unique.
        digest = hashlib.sha1(filepath.encode("utf-8")).hexdigest()[:12]
        prefix_len = _MAX_FILENAME_BYTES - 17  # reserve "__" + 12-hex + ".md"
        stem = f"{name[:prefix_len]}__{digest}.md"
    return stem


def _with_hash_suffix(stem: str, filepath: str) -> str:
    """Append a short hash to *stem* while respecting filesystem limits."""
    digest = hashlib.sha1(filepath.encode("utf-8")).hexdigest()[:12]
    base = stem[:-3] if stem.endswith(".md") else stem
    if len(base) + 17 > _MAX_FILENAME_BYTES:  # "__" + 12-hex + ".md"
        base = base[: _MAX_FILENAME_BYTES - 17]
    return f"{base}__{digest}.md"


class MarkdownGenerator:
    """Write .rag_kb module files and project index."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.output_dir = self.root / ".rag_kb"
        self.modules_dir = self.output_dir / "modules"
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(default=False),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate(self, parsed_files: list[ParsedFile]) -> Path:
        self.modules_dir.mkdir(parents=True, exist_ok=True)

        module_template = self.env.get_template("module.md.j2")
        index_template = self.env.get_template("project_index.md.j2")
        kb_name_map = self._build_kb_name_map(parsed_files)

        for pf in parsed_files:
            kb_name = kb_name_map[pf.filepath]
            out_path = self.modules_dir / kb_name
            content = module_template.render(
                filepath=pf.filepath,
                language=pf.language,
                imports=pf.imports,
                exports=pf.exports,
                internal_dependencies=pf.internal_dependencies,
                external_calls=pf.external_calls,
                external_urls=pf.external_urls,
                database_endpoints=pf.database_endpoints,
                module_docstring=pf.module_docstring,
                classes=pf.classes,
                functions=pf.functions,
            )
            out_path.write_text(content, encoding="utf-8")
            logger.debug("Wrote %s", out_path)

        index_path = self._write_project_index(parsed_files, index_template, kb_name_map)
        logger.info("Knowledge base written to %s", self.output_dir)
        return index_path

    def _build_kb_name_map(self, parsed_files: list[ParsedFile]) -> dict[str, str]:
        """Return filepath -> unique markdown filename mapping."""
        kb_name_map: dict[str, str] = {}
        used_names: set[str] = set()

        for pf in sorted(parsed_files, key=lambda p: p.filepath):
            kb_name = sanitize_filename(pf.filepath)
            if kb_name in used_names:
                kb_name = _with_hash_suffix(kb_name, pf.filepath)
            used_names.add(kb_name)
            kb_name_map[pf.filepath] = kb_name

        return kb_name_map

    def _write_project_index(
        self,
        parsed_files: list[ParsedFile],
        template,
        kb_name_map: dict[str, str],
    ) -> Path:
        language_counts = Counter(pf.language for pf in parsed_files)

        dependency_edges = []
        for pf in parsed_files:
            for dep in pf.internal_dependencies:
                dependency_edges.append({"source": pf.filepath, "target": dep})

        file_index = []
        for pf in sorted(parsed_files, key=lambda p: p.filepath):
            kb_name = kb_name_map[pf.filepath]
            file_index.append(
                {
                    "filepath": pf.filepath,
                    "language": pf.language,
                    "class_count": len(pf.classes),
                    "function_count": len(pf.functions) + sum(len(c.methods) for c in pf.classes),
                    "kb_name": kb_name,
                }
            )

        stack = detect_stack(self.root)
        project_name = self.root.name

        all_urls = []
        all_db = []
        seen_url: set[str] = set()
        for pf in parsed_files:
            for u in pf.external_urls:
                if u.url not in seen_url:
                    seen_url.add(u.url)
                    all_urls.append({"url": u.url, "file": pf.filepath, "line": u.line})
            for db in pf.database_endpoints:
                all_db.append(
                    {
                        "file": pf.filepath,
                        "type": db.connection_type,
                        "host": db.host,
                        "port": db.port,
                        "user": db.user,
                        "schema": db.schema,
                        "database": db.database,
                        "line": db.line,
                        "raw": db.raw_redacted or db.context,
                    }
                )

        content = template.render(
            root=str(self.root),
            project_name=project_name,
            file_count=len(parsed_files),
            language_counts=dict(sorted(language_counts.items())),
            dependency_edges=dependency_edges,
            file_index=file_index,
            stack=stack,
            external_urls=all_urls,
            database_endpoints=all_db,
        )

        index_path = self.output_dir / "project_index.md"
        index_path.write_text(content, encoding="utf-8")
        return index_path
