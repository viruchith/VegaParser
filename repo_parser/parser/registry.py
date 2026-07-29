"""Language registry mapping file extensions to tree-sitter languages."""

from __future__ import annotations

from pathlib import Path

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyw": "python",
    # JavaScript / TypeScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    # Systems languages
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    # JVM / .NET
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".cs": "csharp",
    # Scripting
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    # Data / config
    ".yaml": "yaml",
    ".yml": "yaml",
    ".properties": "properties",
    ".ini": "ini",
    ".cfg": "ini",
    ".tf": "terraform",
    ".tfvars": "terraform",
    ".hcl": "hcl",
    # SQL
    ".sql": "sql",
    ".plsql": "plsql",
    ".pls": "plsql",
    ".pkb": "plsql",
    ".pks": "plsql",
    # Docker
    ".dockerfile": "dockerfile",
}

FILENAME_TO_LANGUAGE: dict[str, str] = {
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
    "makefile": "bash",
    "gnumakefile": "bash",
    "cmakelists.txt": "cpp",
    "docker-compose.yml": "yaml",
    "docker-compose.yaml": "yaml",
    "compose.yml": "yaml",
    "compose.yaml": "yaml",
}

LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "go": "go",
    "golang": "go",
    "rs": "rust",
    "rust": "rust",
    "java": "java",
    "rb": "ruby",
    "ruby": "ruby",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cs": "csharp",
    "csharp": "csharp",
    "c#": "csharp",
    "kt": "kotlin",
    "kotlin": "kotlin",
    "scala": "scala",
    "swift": "swift",
    "php": "php",
    "docker": "dockerfile",
    "dockerfile": "dockerfile",
    "k8s": "kubernetes",
    "kubernetes": "kubernetes",
    "yaml": "yaml",
    "yml": "yaml",
    "sql": "sql",
    "plsql": "plsql",
    "pls": "plsql",
    "terraform": "terraform",
    "tf": "terraform",
    "hcl": "hcl",
    "bash": "bash",
    "shell": "bash",
    "sh": "bash",
    "env": "env",
    "properties": "env",
    "ini": "env",
}


def detect_language(filepath: str) -> str | None:
    path = Path(filepath)
    name_lower = path.name.lower()
    if name_lower == ".env" or name_lower.startswith(".env."):
        return "env"
    if name_lower in FILENAME_TO_LANGUAGE:
        return FILENAME_TO_LANGUAGE[name_lower]
    suffix = path.suffix.lower()
    if suffix == ".env":
        return "env"
    return EXTENSION_TO_LANGUAGE.get(suffix)


def extensions_for_languages(languages: set[str]) -> set[str] | None:
    normalized = {LANGUAGE_ALIASES.get(lang.lower(), lang.lower()) for lang in languages}
    # kubernetes maps to yaml files
    if "kubernetes" in normalized:
        normalized.add("yaml")
    exts = {ext for ext, lang in EXTENSION_TO_LANGUAGE.items() if lang in normalized}
    # Include extensionless Dockerfile when dockerfile filter is active
    if "dockerfile" in normalized:
        return exts  # scanner handles filenames separately
    return exts if exts else None


def normalize_language_filter(raw: str) -> set[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return {LANGUAGE_ALIASES.get(p, p) for p in parts}
