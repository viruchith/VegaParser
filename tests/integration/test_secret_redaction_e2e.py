"""End-to-end: planted secrets must not appear in any output surface.

Runs the real CLI (``init`` then ``bundle``) on a small repository that plants
secrets in Python, TypeScript, Java, .env, YAML and requirements files. Then
reads every file under ``.rag_kb/``, the log file, and the console output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from repo_parser.cli import app

PLANTED_VALUES = [
    "S3cret",
    "pa55",
    "AKIAQ7X3M2N8P5R1T4V6",
    "hunter2-hunter2",
    "sk_live_Zq8Lm3Rt7Vb2Nx9Kp4Wc6Yd1",
    "Tok3nValueXyz",
    "PartnerKey0987654321",
    "JavaSecretToken123",
    "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "EnvPassw0rd!",
    "eyJhbGciOiJIUzI1NiJ9",
    "Yaml-Passw0rd-Value",
    "CiToken9876",
    "BEGIN RSA PRIVATE KEY",
]

FILES = {
    "app/settings.py": '''"""Settings module. Staging key: AKIAQ7X3M2N8P5R1T4V6"""

DB_URL = "postgresql://app_user:S3cret@pa55@db.prod.internal:5432/orders"
PASSWORD = "hunter2-hunter2"
SIGNING_KEY = "-----BEGIN RSA PRIVATE KEY-----"


def connect(api_key="sk_live_Zq8Lm3Rt7Vb2Nx9Kp4Wc6Yd1"):
    """Connect with the default key."""
    return api_key
''',
    "web/client.ts": '''export const REPORTING = "https://svc:Tok3nValueXyz@api.internal.example/v1";

export async function fetchPartner() {
  return fetch("https://api.partner.example/items?api_key=PartnerKey0987654321");
}
''',
    "src/main/java/com/example/Client.java": '''package com.example;

public class Client {
    private static final String CATALOG = "https://api.catalog.example/books?token=JavaSecretToken123";
    private static final String GITHUB = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8";
}
''',
    "config/.env": (
        "DATABASE_URL=postgresql://svc:EnvPassw0rd!@db.env.example:5432/app\n"
        "JWT_TOKEN=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U\n"
    ),
    "deploy/app.yaml": (
        "database:\n"
        "  host: db.yaml.example\n"
        '  password: "Yaml-Passw0rd-Value"\n'
    ),
    "requirements.txt": (
        "internal-lib @ https://ci:CiToken9876@pypi.internal.example/simple/internal_lib-1.0.tar.gz\n"
        "requests==2.32.0\n"
    ),
}


@pytest.fixture
def planted_run(tmp_path, monkeypatch):
    """Run init (with --report-secrets and --verbose) and bundle on a planted repo."""
    repo = tmp_path / "repo"
    for rel, content in FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)  # the log file is written to the current directory

    runner = CliRunner()
    init_result = runner.invoke(app, ["init", str(repo), "--report-secrets", "--verbose"])
    bundle_result = runner.invoke(app, ["bundle", str(repo)])
    return {
        "repo": repo,
        "workdir": workdir,
        "init": init_result,
        "bundle": bundle_result,
    }


def _read_surfaces(run: dict) -> dict[str, str]:
    """Collect every text output: console, log and every file under .rag_kb/."""
    surfaces = {
        "console": run["init"].output + run["bundle"].output,
        "log": (run["workdir"] / "repo-parser.log").read_text(encoding="utf-8"),
    }
    rag_kb = run["repo"] / ".rag_kb"
    for path in sorted(rag_kb.rglob("*")):
        if path.is_file():
            surfaces[path.relative_to(run["repo"]).as_posix()] = path.read_text(encoding="utf-8")
    return surfaces


def test_commands_succeed(planted_run):
    assert planted_run["init"].exit_code == 0, planted_run["init"].output
    assert planted_run["bundle"].exit_code == 0, planted_run["bundle"].output


def test_no_planted_secret_appears_in_any_output_surface(planted_run):
    surfaces = _read_surfaces(planted_run)
    assert any(name.endswith("parse_cache.json") for name in surfaces), "cache must be checked"
    assert any(name.endswith("full_repo_context.md") for name in surfaces), "bundle must be checked"
    assert any(name.startswith(".rag_kb/modules/") for name in surfaces), "module docs must be checked"
    for name, text in surfaces.items():
        for secret in PLANTED_VALUES:
            assert secret not in text, f"{secret!r} leaked into {name}"


def test_outputs_still_show_useful_non_secret_context(planted_run):
    """Positive control: redaction must remove values, not the whole output."""
    surfaces = _read_surfaces(planted_run)
    index = surfaces[".rag_kb/project_index.md"]
    assert "db.prod.internal" in index
    assert "db.env.example" in index
    assert "api.catalog.example" in index, "Java URLs are now extracted and must be listed"
    assert "***" in index


def test_report_lists_categories_and_lines_without_values(planted_run):
    output = planted_run["init"].output
    assert "Likely secrets redacted:" in output
    for category in (
        "url-credentials",
        "url-query-secret",
        "aws-access-key",
        "github-token",
        "stripe-live-key",
        "jwt",
        "private-key",
        "credential-assignment",
    ):
        assert category in output, f"{category} missing from --report-secrets output"
    for secret in PLANTED_VALUES:
        assert secret not in output


def test_secret_findings_are_stored_for_the_cache(planted_run):
    cache_text = (planted_run["repo"] / ".rag_kb" / "parse_cache.json").read_text(encoding="utf-8")
    assert '"category": "aws-access-key"' in cache_text
    assert '"line": 1' in cache_text
