"""Shared data models for parsed repository artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionInfo:
    name: str
    signature: str
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_method: bool = False
    parent_class: str | None = None
    line_start: int = 0
    line_end: int = 0
    internal_calls: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    name: str
    docstring: str | None = None
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    methods: list[FunctionInfo] = field(default_factory=list)
    line_start: int = 0
    line_end: int = 0


@dataclass
class ExternalCall:
    pattern: str
    line: int
    context: str


@dataclass
class ExternalUrl:
    url: str
    line: int
    context: str


@dataclass
class DatabaseEndpoint:
    connection_type: str
    host: str | None = None
    port: str | None = None
    user: str | None = None
    schema: str | None = None
    database: str | None = None
    line: int = 0
    context: str = ""
    raw_redacted: str = ""


@dataclass
class ParsedFile:
    filepath: str
    language: str
    module_docstring: str | None = None
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    external_calls: list[ExternalCall] = field(default_factory=list)
    external_urls: list[ExternalUrl] = field(default_factory=list)
    database_endpoints: list[DatabaseEndpoint] = field(default_factory=list)
    internal_dependencies: list[str] = field(default_factory=list)
