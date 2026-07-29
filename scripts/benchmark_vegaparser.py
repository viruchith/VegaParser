#!/usr/bin/env python3
"""Benchmark VegaParser on a curated set of popular GitHub repositories."""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "main.py"
DEFAULT_WORKSPACE = Path.home() / ".cache" / "vegaparser-benchmarks"


@dataclass(frozen=True)
class BenchmarkTarget:
    id: str
    repo: str
    tier: str
    languages: tuple[str, ...]
    notes: str = ""


@dataclass
class RunResult:
    duration_seconds: float
    returncode: int
    module_count: int
    stderr_tail: str = ""


SUITE: list[BenchmarkTarget] = [
    BenchmarkTarget("python-heavy", "https://github.com/pandas-dev/pandas", "heavy", ("python",), "Large Python analytics repo"),
    BenchmarkTarget("python-light", "https://github.com/psf/requests", "light", ("python",), "Small, popular Python client"),
    BenchmarkTarget("javascript-heavy", "https://github.com/facebook/react", "heavy", ("javascript",), "Large JS/TS frontend repo"),
    BenchmarkTarget("javascript-light", "https://github.com/preactjs/preact", "light", ("javascript",), "Compact JS frontend repo"),
    BenchmarkTarget("typescript-heavy", "https://github.com/microsoft/vscode", "heavy", ("typescript",), "Large TypeScript application"),
    BenchmarkTarget("typescript-light", "https://github.com/sindresorhus/type-fest", "light", ("typescript",), "TypeScript utility types"),
    BenchmarkTarget("go-heavy", "https://github.com/kubernetes/kubernetes", "heavy", ("go",), "Large Go codebase"),
    BenchmarkTarget("go-light", "https://github.com/gorilla/mux", "light", ("go",), "Small Go routing library"),
    BenchmarkTarget("rust-heavy", "https://github.com/rust-lang/rust", "heavy", ("rust",), "Rust compiler and tools"),
    BenchmarkTarget("rust-light", "https://github.com/BurntSushi/ripgrep", "light", ("rust",), "Popular Rust CLI"),
    BenchmarkTarget("c-heavy", "https://github.com/curl/curl", "heavy", ("c",), "Large C codebase (HTTP client)"),
    BenchmarkTarget("c-light", "https://github.com/libuv/libuv", "light", ("c",), "Async I/O library"),
    BenchmarkTarget("cpp-heavy", "https://github.com/grpc/grpc", "heavy", ("cpp",), "Large C++ RPC framework"),
    BenchmarkTarget("cpp-light", "https://github.com/fmtlib/fmt", "light", ("cpp",), "Small, popular C++ formatting lib"),
    BenchmarkTarget("java-heavy", "https://github.com/google/gson", "heavy", ("java",), "User-provided Java heavy repo"),
    BenchmarkTarget("java-light", "https://github.com/stleary/json-java", "light", ("java",), "User-provided Java light repo"),
    BenchmarkTarget("kotlin-heavy", "https://github.com/JetBrains/kotlin", "heavy", ("kotlin",), "Kotlin compiler repo"),
    BenchmarkTarget("kotlin-light", "https://github.com/square/okhttp", "light", ("kotlin",), "Widely used Kotlin/Java client"),
    BenchmarkTarget("scala-heavy", "https://github.com/apache/spark", "heavy", ("scala",), "Large Scala/Spark codebase"),
    BenchmarkTarget("scala-light", "https://github.com/scalatest/scalatest", "light", ("scala",), "Scala testing library"),
    BenchmarkTarget("csharp-heavy", "https://github.com/dotnet/runtime", "heavy", ("csharp",), "Large .NET runtime repo"),
    BenchmarkTarget("csharp-light", "https://github.com/JamesNK/Newtonsoft.Json", "light", ("csharp",), "Popular C# JSON library"),
    BenchmarkTarget("ruby-heavy", "https://github.com/rails/rails", "heavy", ("ruby",), "Large Ruby web framework"),
    BenchmarkTarget("ruby-light", "https://github.com/sinatra/sinatra", "light", ("ruby",), "Compact Ruby web framework"),
    BenchmarkTarget("php-heavy", "https://github.com/laravel/framework", "heavy", ("php",), "Large PHP framework"),
    BenchmarkTarget("php-light", "https://github.com/Seldaek/monolog", "light", ("php",), "Popular PHP logging library"),
    BenchmarkTarget("swift-heavy", "https://github.com/apple/swift", "heavy", ("swift",), "Swift compiler and stdlib"),
    BenchmarkTarget("swift-light", "https://github.com/Alamofire/Alamofire", "light", ("swift",), "Popular Swift networking lib"),
    BenchmarkTarget("yaml-heavy", "https://github.com/kubernetes/kubernetes", "heavy", ("yaml", "kubernetes"), "YAML and K8s manifests"),
    BenchmarkTarget("yaml-light", "https://github.com/kubernetes-sigs/kustomize", "light", ("yaml", "kubernetes"), "Smaller K8s/YAML repo"),
    BenchmarkTarget("terraform-heavy", "https://github.com/hashicorp/terraform", "heavy", ("terraform", "hcl"), "Terraform/HCL heavy repo"),
    BenchmarkTarget("terraform-light", "https://github.com/terraform-aws-modules/terraform-aws-vpc", "light", ("terraform", "hcl"), "Popular Terraform module"),
    BenchmarkTarget("dockerfile-heavy", "https://github.com/moby/moby", "heavy", ("dockerfile",), "Large Docker codebase"),
    BenchmarkTarget("dockerfile-light", "https://github.com/docker-library/hello-world", "light", ("dockerfile",), "Small Dockerfile repo"),
    BenchmarkTarget("bash-heavy", "https://github.com/ohmyzsh/ohmyzsh", "heavy", ("bash", "shell"), "Large shell-script repo"),
    BenchmarkTarget("bash-light", "https://github.com/junegunn/fzf", "light", ("bash", "shell"), "Popular shell integration repo"),
    BenchmarkTarget("sql-heavy", "https://github.com/dbt-labs/dbt-core", "heavy", ("sql",), "SQL-centric analytics repo"),
    BenchmarkTarget("sql-light", "https://github.com/dbt-labs/jaffle-shop", "light", ("sql",), "Small SQL benchmark repo"),
    BenchmarkTarget("plsql-heavy", "https://github.com/oracle-samples/db-sample-schemas", "heavy", ("plsql", "sql"), "Oracle sample schemas"),
    BenchmarkTarget("plsql-light", "https://github.com/utPLSQL/utPLSQL", "light", ("plsql", "sql"), "Popular PL/SQL test framework"),
    BenchmarkTarget("config-heavy", "https://github.com/spring-projects/spring-boot", "heavy", ("env", "properties", "ini"), "Config-heavy Java repo"),
    BenchmarkTarget("config-light", "https://github.com/psf/requests", "light", ("env", "properties", "ini"), "Small repo with common config files"),
]


def slug_from_repo(repo_url: str) -> str:
    owner_repo = repo_url.rstrip("/").rsplit("/", 2)[-2:]
    return "-".join(owner_repo).removesuffix(".git")


def clone_path(workspace: Path, target: BenchmarkTarget) -> Path:
    return workspace / slug_from_repo(target.repo)


def repo_exists(path: Path) -> bool:
    return (path / ".git").is_dir()


def ensure_repo(target: BenchmarkTarget, workspace: Path, refresh: bool = False, verbose: bool = False) -> Path:
    path = clone_path(workspace, target)
    if refresh and path.exists():
        if verbose:
            print(f"  - removing existing clone: {path}", flush=True)
        shutil.rmtree(path)
    if path.exists() and not repo_exists(path):
        if verbose:
            print(f"  - removing partial clone: {path}", flush=True)
        shutil.rmtree(path)
    if repo_exists(path):
        if verbose:
            print(f"  - using existing clone: {path}", flush=True)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"  - cloning {target.repo} -> {path}", flush=True)
    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        target.repo,
        str(path),
    ]
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "git clone failed").strip())
    return path


def clear_generated_outputs(repo_path: Path) -> None:
    rag_kb = repo_path / ".rag_kb"
    if rag_kb.exists():
        try:
            shutil.rmtree(rag_kb)
        except OSError:
            # Python's fd-based rmtree can raise ENOTEMPTY on macOS when the
            # directory contains entries it couldn't fully clean (e.g. files
            # whose names were generated before the filename-length fix).
            # Fall back to the OS rm command which is more resilient.
            subprocess.run(["rm", "-rf", str(rag_kb)], check=False)
    log_path = repo_path / "repo-parser.log"
    if log_path.exists():
        log_path.unlink()


def run_vegaparser(repo_path: Path, languages: tuple[str, ...]) -> RunResult:
    cmd = [sys.executable, str(MAIN_PY), "init", "."]
    if languages:
        cmd.extend(["--languages", ",".join(languages)])

    start = time.perf_counter()
    completed = subprocess.run(
        cmd,
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    duration = time.perf_counter() - start

    module_count = len(list((repo_path / ".rag_kb" / "modules").glob("*.md")))
    stderr_tail = "\n".join((completed.stderr or "").splitlines()[-8:])
    return RunResult(
        duration_seconds=duration,
        returncode=completed.returncode,
        module_count=module_count,
        stderr_tail=stderr_tail,
    )


def run_target(
    target: BenchmarkTarget,
    workspace: Path,
    repeat: int,
    warm: bool,
    refresh: bool,
    verbose: bool = False,
) -> dict:
    try:
        repo_path = ensure_repo(target, workspace, refresh=refresh, verbose=verbose)
    except Exception as exc:
        return {
            "id": target.id,
            "repo": target.repo,
            "tier": target.tier,
            "languages": ",".join(target.languages),
            "notes": target.notes,
            "modules": 0,
            "status": f"clone failed: {exc}",
            "cold_avg_s": None,
            "cold_min_s": None,
            "cold_max_s": None,
            "warm_s": None,
            "workspace": "",
        }

    cold_times: list[float] = []
    module_count = 0
    last_rc = 0
    last_error = ""

    for run_no in range(1, repeat + 1):
        if verbose:
            print(f"  - cold run {run_no}/{repeat}", flush=True)
        clear_generated_outputs(repo_path)
        result = run_vegaparser(repo_path, target.languages)
        cold_times.append(result.duration_seconds)
        module_count = result.module_count
        last_rc = result.returncode
        if verbose:
            print(
                f"    completed in {result.duration_seconds:.2f}s (modules={module_count}, rc={last_rc})",
                flush=True,
            )
        if last_rc != 0:
            last_error = "cold run failed"
            if result.stderr_tail:
                print(result.stderr_tail, file=sys.stderr, flush=True)
            break

    warm_seconds = None
    if warm and last_rc == 0:
        if verbose:
            print("  - warm run", flush=True)
        result = run_vegaparser(repo_path, target.languages)
        warm_seconds = result.duration_seconds
        module_count = result.module_count
        last_rc = result.returncode
        if verbose:
            print(
                f"    completed in {result.duration_seconds:.2f}s (modules={module_count}, rc={last_rc})",
                flush=True,
            )
        if last_rc != 0:
            last_error = "warm run failed"
            if result.stderr_tail:
                print(result.stderr_tail, file=sys.stderr, flush=True)

    return {
        "id": target.id,
        "repo": target.repo,
        "tier": target.tier,
        "languages": ",".join(target.languages),
        "notes": target.notes,
        "modules": module_count,
        "status": "ok" if last_rc == 0 else last_error or f"exit {last_rc}",
        "cold_avg_s": statistics.mean(cold_times) if cold_times else None,
        "cold_min_s": min(cold_times) if cold_times else None,
        "cold_max_s": max(cold_times) if cold_times else None,
        "warm_s": warm_seconds,
        "workspace": str(repo_path),
    }


def format_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def format_seconds_with_unit(value: float | None) -> str:
    formatted = format_seconds(value)
    return formatted if formatted == "-" else f"{formatted}s"


def short_text(value: str, limit: int = 80) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def print_table(rows: list[dict]) -> None:
    headers = ["id", "tier", "languages", "cold_avg_s", "warm_s", "modules", "status"]
    widths = {header: len(header) for header in headers}
    for row in rows:
        widths["id"] = max(widths["id"], len(row["id"]))
        widths["tier"] = max(widths["tier"], len(row["tier"]))
        widths["languages"] = max(widths["languages"], len(row["languages"]))
        widths["cold_avg_s"] = max(widths["cold_avg_s"], len(format_seconds(row["cold_avg_s"])))
        widths["warm_s"] = max(widths["warm_s"], len(format_seconds(row["warm_s"])))
        widths["modules"] = max(widths["modules"], len(str(row["modules"])))
        widths["status"] = max(widths["status"], len(short_text(row["status"])))

    def line(values: list[str]) -> str:
        return "  ".join(value.ljust(widths[header]) for value, header in zip(values, headers))

    print(line(headers))
    print("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        print(
            line(
                [
                    row["id"],
                    row["tier"],
                    row["languages"],
                    format_seconds(row["cold_avg_s"]),
                    format_seconds(row["warm_s"]),
                    str(row["modules"]),
                    short_text(row["status"]),
                ]
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="Directory used to clone benchmark repos.")
    parser.add_argument("--tier", choices=["heavy", "light", "all"], default="all", help="Only run heavy or light entries.")
    parser.add_argument("--language", action="append", dest="languages", help="Only run entries that include this language filter.")
    parser.add_argument("--repo", action="append", dest="repos", help="Only run specific benchmark ids.")
    parser.add_argument("--repeat", type=int, default=1, help="How many cold runs to average per repo.")
    parser.add_argument("--warm", action="store_true", help="Run a second warm-cache pass after the cold run.")
    parser.add_argument("--refresh", action="store_true", help="Reclone benchmark repositories before running.")
    parser.add_argument("--list", action="store_true", help="List the benchmark suite and exit.")
    parser.add_argument("--json", dest="json_path", type=Path, help="Write benchmark results as JSON.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed per-step progress logs.")
    return parser.parse_args()


def matches(target: BenchmarkTarget, args: argparse.Namespace) -> bool:
    if args.tier != "all" and target.tier != args.tier:
        return False
    if args.repos and target.id not in set(args.repos):
        return False
    if args.languages and not set(args.languages).intersection(target.languages):
        return False
    return True


def main() -> int:
    args = parse_args()
    selected = [target for target in SUITE if matches(target, args)]

    if args.list:
        for target in selected:
            print(f"{target.id:20} {target.tier:5} {','.join(target.languages):24} {target.repo}")
        return 0

    if not selected:
        print("No benchmark targets matched the provided filters.", file=sys.stderr)
        return 2

    args.workspace.mkdir(parents=True, exist_ok=True)
    total = len(selected)
    print(
        f"Running {total} benchmark target(s) (repeat={max(1, args.repeat)}, warm={args.warm}, refresh={args.refresh})",
        flush=True,
    )
    results: list[dict] = []
    for idx, target in enumerate(selected, start=1):
        print(f"[{idx}/{total}] {target.id} ({target.tier})", flush=True)
        result = run_target(
            target,
            args.workspace,
            max(1, args.repeat),
            args.warm,
            args.refresh,
            verbose=args.verbose,
        )
        results.append(result)
        print(
            f"  -> {result['status']} | cold_avg={format_seconds_with_unit(result['cold_avg_s'])} | warm={format_seconds_with_unit(result['warm_s'])} | modules={result['modules']}",
            flush=True,
        )

    print("\nSummary", flush=True)
    print_table(results)

    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
