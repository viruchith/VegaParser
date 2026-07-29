"""Repository file discovery with gitignore and skip rules."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pathspec

from repo_parser.parser.registry import detect_language, extensions_for_languages

logger = logging.getLogger(__name__)

SPECIAL_FILENAMES = {"dockerfile", "containerfile", "makefile", "gnumakefile", "cmakelists.txt"}

SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".rag_kb",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "eggs",
    "*.egg-info",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".class", ".jar",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".sqlite", ".db", ".bin",
}


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True


def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        lines = gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)
    except OSError as exc:
        logger.warning("Could not read .gitignore: %s", exc)
        return None


class RepositoryScanner:
    """Walk a repository and yield parseable source files."""

    def __init__(
        self,
        root: Path,
        languages: set[str] | None = None,
        extensions: set[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.languages = languages
        self.extensions = extensions
        self._gitignore = _load_gitignore(self.root)

    def _should_skip_dir(self, rel_dir_posix: str, name: str) -> bool:
        if name.startswith(".") and name not in {".", ".."}:
            return True
        if name in SKIP_DIRS or name.endswith(".egg-info"):
            return True
        # Prune whole directories matched by .gitignore instead of walking into
        # them and filtering file-by-file (huge win on large repos).
        if self._gitignore is not None and self._gitignore.match_file(rel_dir_posix + "/"):
            return True
        return False

    def _is_ignored(self, rel_path: str) -> bool:
        if self._gitignore is None:
            return False
        return self._gitignore.match_file(rel_path)

    def discover(self) -> list[Path]:
        """Return sorted list of source file paths relative to root."""
        found: list[Path] = []

        for dirpath, dirnames, filenames in os.walk(self.root):
            current_dir = Path(dirpath)
            rel_dir = current_dir.relative_to(self.root)
            rel_dir_posix = "" if rel_dir == Path(".") else rel_dir.as_posix()

            # Prune skip/ignored directories in-place so os.walk never descends
            # into them (e.g. .git, node_modules, venv, build artifacts).
            kept_dirs = []
            for name in dirnames:
                child_rel_posix = f"{rel_dir_posix}/{name}" if rel_dir_posix else name
                if self._should_skip_dir(child_rel_posix, name):
                    continue
                kept_dirs.append(name)
            dirnames[:] = sorted(kept_dirs)

            for filename in sorted(filenames):
                path = current_dir / filename
                rel = path.relative_to(self.root)
                rel_posix = rel.as_posix()

                if self._is_ignored(rel_posix):
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug("Ignored by .gitignore: %s", rel_posix)
                    continue

                # Cheap extension/name checks before touching the filesystem
                # again for a binary-content sniff.
                if not self._is_selected_file(path, rel_posix):
                    logger.debug("Skipped unsupported or filtered file: %s", rel_posix)
                    continue

                if _is_binary(path):
                    logger.debug("Skipped binary file: %s", rel_posix)
                    continue

                found.append(rel)
                logger.debug("Discovered parseable file: %s", rel_posix)

        found.sort()
        logger.info("Discovered %d files in %s", len(found), self.root)
        return found

    def _is_selected_file(self, path: Path, rel_posix: str) -> bool:
        detected = detect_language(rel_posix)
        if detected is None:
            return False

        if self.languages is not None and detected not in self.languages:
            if not (detected == "yaml" and "kubernetes" in self.languages):
                return False

        if self.extensions is None:
            return True

        suffix = path.suffix.lower()
        if suffix in self.extensions:
            return True
        if path.name.lower() in SPECIAL_FILENAMES and "dockerfile" in self.extensions:
            return True
        return False

    def read_file(self, rel_path: Path) -> str | None:
        full = self.root / rel_path
        try:
            return full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read %s: %s", rel_path, exc)
            return None
