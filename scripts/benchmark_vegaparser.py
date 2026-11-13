#!/usr/bin/env python3
"""Benchmark VegaParser on a curated set of popular GitHub repositories."""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "main.py"
DEFAULT_WORKSPACE = Path.home() / ".cache" / "vegaparser-benchmarks"

# Serialises all log output so parallel workers do not interleave lines.
_log_lock = threading.Lock()

# Guards the per-repository lock map used to prevent shared-path races.
_repo_lock_map_guard = threading.Lock()
_repo_lock_map: dict[str, threading.Lock] = {}


def _ts() -> str:
    """Return a compact local timestamp string: HH:MM:SS."""
    return datetime.datetime.now().strftime("%H:%M:%S")


def _iso() -> str:
    """Return an ISO-8601 local timestamp suitable for JSON output."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _log(message: str, *, indent: int = 0, prefix: str = "", stderr: bool = False) -> None:
    """Print a timestamped benchmark log line; thread-safe."""
    stream = sys.stderr if stderr else sys.stdout
    padding = "  " * max(indent, 0)
    tag = f"[{prefix}] " if prefix else ""
    with _log_lock:
        print(f"{padding}[{_ts()}] {tag}{message}", file=stream, flush=True)


def _repo_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _repo_lock_map_guard:
        lock = _repo_lock_map.get(key)
        if lock is None:
            lock = threading.Lock()
            _repo_lock_map[key] = lock
    return lock


@contextmanager
def _acquire_repo_lock(repo_path: Path, *, target_id: str, verbose: bool) -> Iterator[None]:
    """Serialise targets that resolve to the same clone path."""
    lock = _repo_lock_for(repo_path)
    if verbose:
        _log(f"waiting for repository lock: {repo_path}", indent=1, prefix=target_id)
    lock.acquire()
    try:
        if verbose:
            _log(f"acquired repository lock: {repo_path}", indent=1, prefix=target_id)
        yield
    finally:
        lock.release()
        if verbose:
            _log(f"released repository lock: {repo_path}", indent=1, prefix=target_id)


@contextmanager
def _acquire_heavy_slot(
    target: BenchmarkTarget,
    heavy_slots: threading.Semaphore | None,
    *,
    verbose: bool,
) -> Iterator[None]:
    """Limit heavy-target parse concurrency to reduce extreme contention."""
    if target.tier != "heavy" or heavy_slots is None:
        yield
        return

    if verbose:
        _log("waiting for heavy-worker slot", indent=1, prefix=target.id)
    heavy_slots.acquire()
    try:
        if verbose:
            _log("acquired heavy-worker slot", indent=1, prefix=target.id)
        yield
    finally:
        heavy_slots.release()
        if verbose:
            _log("released heavy-worker slot", indent=1, prefix=target.id)


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
    index_present: bool = False
    # Peak working set of the child process. None when the platform cannot report it.
    peak_rss_bytes: int | None = None
    # Total bytes written under .rag_kb after the run.
    output_bytes: int = 0


RSS_POLL_SECONDS = 0.1
"""How often the child's memory is sampled. Peaks shorter than this can be missed."""

# Never copied when a local path is benchmarked: VCS data, virtualenvs, caches and outputs.
LOCAL_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".rag_kb", "repo-parser.log"
)


def _windows_tree_peak_rss(root_pid: int) -> int | None:
    """Sum of peak working sets over root_pid and its descendants (ctypes, no psutil).

    The venv's python.exe is a launcher that starts the real interpreter as a child process,
    so the root process alone under-reports. Summing per-process peaks can overstate the true
    simultaneous peak; that is the documented, conservative direction.
    """
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    th32cs_snapprocess = 0x00000002
    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE

    # Map parent -> children from a process snapshot.
    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return None
    children: dict[int, list[int]] = {}
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(ProcessEntry32)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    tree = [root_pid]
    for pid in tree:  # breadth-first walk; the list grows while iterating
        tree.extend(children.get(pid, []))

    total = 0
    measured = False
    for pid in tree:
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            continue  # the process may have exited, or access may be denied
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(ProcessMemoryCounters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                total += int(counters.PeakWorkingSetSize)
                measured = True
        finally:
            kernel32.CloseHandle(handle)
    return total if measured else None


def _peak_rss_bytes(pid: int) -> int | None:
    """Best available peak/current memory for a live process tree. None when unavailable."""
    if sys.platform == "win32":
        return _windows_tree_peak_rss(pid)
    status = Path(f"/proc/{pid}/status")
    if status.is_file():
        # Linux keeps the peak itself (VmHWM, in kB).
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
        return None
    # Other POSIX (e.g. macOS): sample the current RSS (kB) and keep the max of the samples.
    completed = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    value = completed.stdout.strip()
    return int(value) * 1024 if value.isdigit() else None


def _output_bytes(rag_kb: Path) -> int:
    if not rag_kb.is_dir():
        return 0
    return sum(p.stat().st_size for p in rag_kb.rglob("*") if p.is_file())


def _max_optional(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


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
    BenchmarkTarget("dockerfile-light", "https://github.com/docker/getting-started", "light", ("dockerfile",), "Small Dockerfile repo"),
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
    if target.tier == LOCAL_TIER:
        return workspace / "local" / target.id
    return workspace / slug_from_repo(target.repo)


LOCAL_TIER = "local"
"""Tier used for `--local PATH` targets. They are copied into the workspace, never run in place."""


def local_target(path: Path) -> BenchmarkTarget:
    resolved = path.resolve()
    return BenchmarkTarget(
        id=f"local-{resolved.name}",
        repo=str(resolved),
        tier=LOCAL_TIER,
        languages=(),
        notes=f"Local copy of {resolved}",
    )


def _copy_local(target: BenchmarkTarget, workspace: Path, verbose: bool) -> Path:
    """Copy a local directory into the workspace so the user's tree is never written to."""
    source = Path(target.repo)
    if not source.is_dir():
        raise RuntimeError(f"not a directory: {source}")
    dest = clone_path(workspace, target)
    if dest.exists():
        shutil.rmtree(dest)
    if verbose:
        _log(f"copying {source} -> {dest}", indent=1, prefix=target.id)
    shutil.copytree(source, dest, ignore=LOCAL_COPY_IGNORE)
    return dest


def artifact_path(workspace: Path, target: BenchmarkTarget) -> Path:
    return workspace / ".benchmark-artifacts" / target.id


def repo_exists(path: Path) -> bool:
    return (path / ".git").is_dir()


def ensure_repo(
    target: BenchmarkTarget,
    workspace: Path,
    refresh: bool = False,
    verbose: bool = False,
    shutdown: threading.Event | None = None,
) -> Path:
    if target.tier == LOCAL_TIER:
        # Always re-copy so the benchmark sees the current state of the local tree.
        return _copy_local(target, workspace, verbose)
    path = clone_path(workspace, target)
    if refresh and path.exists():
        if verbose:
            _log(f"removing existing clone: {path}", indent=1, prefix=target.id)
        shutil.rmtree(path)
    if path.exists() and not repo_exists(path):
        if verbose:
            _log(f"removing partial clone: {path}", indent=1, prefix=target.id)
        shutil.rmtree(path)
    if repo_exists(path):
        if verbose:
            _log(f"using existing clone: {path}", indent=1, prefix=target.id)
        return path

    if shutdown is not None and shutdown.is_set():
        raise RuntimeError("shutdown requested before clone")

    path.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        _log(f"cloning {target.repo} -> {path}", indent=1, prefix=target.id)
    cmd = [
        "git",
        "-c",
        "core.longpaths=true",
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
            # Python fd-based rmtree can raise ENOTEMPTY on macOS; fall back to os rm.
            subprocess.run(["rm", "-rf", str(rag_kb)], check=False)
    log_path = repo_path / "repo-parser.log"
    if log_path.exists():
        log_path.unlink()


def run_vegaparser(repo_path: Path, languages: tuple[str, ...]) -> RunResult:
    cmd = [sys.executable, str(MAIN_PY), "init", "."]
    if languages:
        cmd.extend(["--languages", ",".join(languages)])

    # stderr goes to a temp file so the child never blocks on a full pipe while we sample it.
    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        start = time.perf_counter()
        proc = subprocess.Popen(cmd, cwd=repo_path, stdout=subprocess.DEVNULL, stderr=stderr_file)
        peak: int | None = None
        while proc.poll() is None:
            sample = _peak_rss_bytes(proc.pid)
            if sample is not None and (peak is None or sample > peak):
                peak = sample
            time.sleep(RSS_POLL_SECONDS)
        duration = time.perf_counter() - start
        stderr_file.seek(0)
        stderr_text = stderr_file.read().decode("utf-8", errors="replace")

    rag_kb = repo_path / ".rag_kb"
    module_count = len(list((rag_kb / "modules").glob("*.md")))
    stderr_tail = "\n".join(stderr_text.splitlines()[-8:])
    return RunResult(
        duration_seconds=duration,
        returncode=proc.returncode,
        module_count=module_count,
        stderr_tail=stderr_tail,
        index_present=(rag_kb / "project_index.md").is_file(),
        peak_rss_bytes=peak,
        output_bytes=_output_bytes(rag_kb),
    )


def snapshot_target_outputs(
    repo_path: Path,
    workspace: Path,
    target: BenchmarkTarget,
    *,
    verbose: bool,
) -> tuple[str, str]:
    """Copy benchmark artifacts to a target-specific snapshot directory."""
    target_dir = artifact_path(workspace, target)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    rag_source = repo_path / ".rag_kb"
    rag_snapshot = target_dir / ".rag_kb"
    if rag_source.is_dir():
        shutil.copytree(rag_source, rag_snapshot)
        rag_snapshot_str = str(rag_snapshot)
    else:
        rag_snapshot_str = ""

    log_source = repo_path / "repo-parser.log"
    log_snapshot = target_dir / "repo-parser.log"
    if log_source.is_file():
        shutil.copy2(log_source, log_snapshot)
        log_snapshot_str = str(log_snapshot)
    else:
        log_snapshot_str = ""

    if verbose:
        _log(f"artifact snapshot: {target_dir}", indent=1, prefix=target.id)
    return rag_snapshot_str, log_snapshot_str


def _cancelled_result(target: BenchmarkTarget, started_at: str) -> dict:
    return {
        "id": target.id,
        "repo": target.repo,
        "tier": target.tier,
        "languages": ",".join(target.languages),
        "notes": target.notes,
        "modules": 0,
        "status": "cancelled",
        "cold_avg_s": None,
        "cold_min_s": None,
        "cold_max_s": None,
        "warm_s": None,
        "peak_rss_bytes": None,
        "output_bytes": 0,
        "workspace": "",
        "artifact_rag_kb": "",
        "artifact_log": "",
        "started_at": started_at,
        "finished_at": _iso(),
    }


def run_target(
    target: BenchmarkTarget,
    workspace: Path,
    repeat: int,
    warm: bool,
    refresh: bool,
    verbose: bool = False,
    shutdown: threading.Event | None = None,
    heavy_slots: threading.Semaphore | None = None,
) -> dict:
    target_started_at = _iso()
    repo_path = clone_path(workspace, target)

    if shutdown is not None and shutdown.is_set():
        return _cancelled_result(target, target_started_at)

    with _acquire_repo_lock(repo_path, target_id=target.id, verbose=verbose):
        if shutdown is not None and shutdown.is_set():
            return _cancelled_result(target, target_started_at)

        try:
            repo_path = ensure_repo(
                target, workspace, refresh=refresh, verbose=verbose, shutdown=shutdown
            )
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
                "peak_rss_bytes": None,
                "output_bytes": 0,
                "workspace": "",
                "artifact_rag_kb": "",
                "artifact_log": "",
                "started_at": target_started_at,
                "finished_at": _iso(),
            }

        cold_times: list[float] = []
        module_count = 0
        index_present = False
        output_bytes = 0
        peak_rss_bytes: int | None = None
        last_rc = 0
        last_error = ""

        with _acquire_heavy_slot(target, heavy_slots, verbose=verbose):
            for run_no in range(1, repeat + 1):
                if shutdown is not None and shutdown.is_set():
                    last_error = "interrupted"
                    break
                if verbose:
                    _log(f"cold run {run_no}/{repeat} started", indent=1, prefix=target.id)
                clear_generated_outputs(repo_path)
                result = run_vegaparser(repo_path, target.languages)
                cold_times.append(result.duration_seconds)
                module_count = result.module_count
                index_present = result.index_present
                output_bytes = result.output_bytes
                peak_rss_bytes = _max_optional(peak_rss_bytes, result.peak_rss_bytes)
                last_rc = result.returncode
                if verbose:
                    _log(
                        f"cold run {run_no}/{repeat} finished in {result.duration_seconds:.2f}s"
                        f" (modules={module_count}, rc={last_rc})",
                        indent=1,
                        prefix=target.id,
                    )
                if last_rc != 0:
                    last_error = "cold run failed"
                    if result.stderr_tail:
                        _log(result.stderr_tail, prefix=target.id, stderr=True)
                    break

            warm_seconds = None
            if warm and last_rc == 0 and not (shutdown is not None and shutdown.is_set()):
                if verbose:
                    _log("warm run started", indent=1, prefix=target.id)
                result = run_vegaparser(repo_path, target.languages)
                warm_seconds = result.duration_seconds
                module_count = result.module_count
                index_present = result.index_present
                output_bytes = result.output_bytes
                peak_rss_bytes = _max_optional(peak_rss_bytes, result.peak_rss_bytes)
                last_rc = result.returncode
                if verbose:
                    _log(
                        f"warm run finished in {result.duration_seconds:.2f}s"
                        f" (modules={module_count}, rc={last_rc})",
                        indent=1,
                        prefix=target.id,
                    )
                if last_rc != 0:
                    last_error = "warm run failed"
                    if result.stderr_tail:
                        _log(result.stderr_tail, prefix=target.id, stderr=True)

        # Mark interrupted only when we exited before completing all intended cold runs.
        if shutdown is not None and shutdown.is_set() and not last_error and len(cold_times) < repeat:
            last_error = "interrupted"
        # A run that exits 0 but writes nothing useful is a failure, not a success.
        if last_rc == 0 and not last_error and (module_count == 0 or not index_present):
            last_error = "empty output (no modules or no project index)"
        status = "ok" if last_rc == 0 and not last_error else last_error or f"exit {last_rc}"
        artifact_rag_kb, artifact_log = snapshot_target_outputs(
            repo_path, workspace, target, verbose=verbose
        )

        return {
            "id": target.id,
            "repo": target.repo,
            "tier": target.tier,
            "languages": ",".join(target.languages),
            "notes": target.notes,
            "modules": module_count,
            "status": status,
            "cold_avg_s": statistics.mean(cold_times) if cold_times else None,
            "cold_min_s": min(cold_times) if cold_times else None,
            "cold_max_s": max(cold_times) if cold_times else None,
            "warm_s": warm_seconds,
            "peak_rss_bytes": peak_rss_bytes,
            "output_bytes": output_bytes,
            "workspace": str(repo_path),
            "artifact_rag_kb": artifact_rag_kb,
            "artifact_log": artifact_log,
            "started_at": target_started_at,
            "finished_at": _iso(),
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


def format_mb(value: int | None) -> str:
    return "-" if value is None else f"{value / (1024 * 1024):.1f}"


def format_kb(value: int | None) -> str:
    return "-" if value is None else f"{value / 1024:.1f}"


def _table_lines(rows: list[dict]) -> list[str]:
    headers = ["id", "tier", "languages", "cold_avg_s", "warm_s", "peak_mb", "out_kb", "modules", "status"]
    widths = {header: len(header) for header in headers}

    def cells(row: dict) -> list[str]:
        return [
            row["id"],
            row["tier"],
            row["languages"],
            format_seconds(row["cold_avg_s"]),
            format_seconds(row["warm_s"]),
            format_mb(row.get("peak_rss_bytes")),
            format_kb(row.get("output_bytes")),
            str(row["modules"]),
            short_text(row["status"]),
        ]

    for row in rows:
        for header, value in zip(headers, cells(row)):
            widths[header] = max(widths[header], len(value))

    def line(values: list[str]) -> str:
        return "  ".join(value.ljust(widths[header]) for value, header in zip(values, headers))

    lines = [line(headers), "  ".join("-" * widths[h] for h in headers)]
    for row in rows:
        lines.append(line(cells(row)))
    return lines


def print_table(rows: list[dict]) -> None:
    for line in _table_lines(rows):
        print(line)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="Directory used to clone benchmark repos.")
    parser.add_argument("--tier", choices=["heavy", "light", "all"], default="all", help="Only run heavy or light entries.")
    parser.add_argument("--language", action="append", dest="languages", help="Only run entries that include this language filter.")
    parser.add_argument("--repo", action="append", dest="repos", help="Only run specific benchmark ids.")
    parser.add_argument(
        "--local",
        action="append",
        dest="local_paths",
        type=Path,
        metavar="PATH",
        help="Benchmark a local directory, copied into the workspace and never modified. With --local alone, only local paths run.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="How many cold runs to average per repo.")
    parser.add_argument("--warm", action="store_true", help="Run a second warm-cache pass after the cold run.")
    parser.add_argument("--refresh", action="store_true", help="Reclone benchmark repositories before running.")
    parser.add_argument("--list", action="store_true", help="List the benchmark suite and exit.")
    parser.add_argument("--json", dest="json_path", type=Path, help="Write benchmark results as JSON.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed per-step progress logs.")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of benchmark targets to run in parallel (default: 1).",
    )
    parser.add_argument(
        "--max-heavy-workers",
        type=int,
        default=2,
        metavar="N",
        help="Maximum number of heavy targets to run concurrently when workers > 1 (default: 2).",
    )
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
    local_paths = args.local_paths or []
    suite_filtered = bool(args.repos or args.languages or args.tier != "all")
    # --local alone runs only the local paths; add --repo/--tier/--language to include suite entries.
    if local_paths and not suite_filtered:
        selected: list[BenchmarkTarget] = []
    else:
        selected = [target for target in SUITE if matches(target, args)]
    for path in local_paths:
        if not path.is_dir():
            _log(f"--local path is not a directory: {path}", stderr=True)
            return 2
        selected.append(local_target(path))

    if args.list:
        for target in selected:
            print(f"{target.id:20} {target.tier:5} {','.join(target.languages):24} {target.repo}")
        return 0

    if not selected:
        _log("No benchmark targets matched the provided filters.", stderr=True)
        return 2

    workers = max(1, args.workers)
    repeat = max(1, args.repeat)
    max_heavy_workers = max(1, args.max_heavy_workers)
    has_heavy_targets = any(target.tier == "heavy" for target in selected)
    heavy_workers = min(workers, max_heavy_workers) if has_heavy_targets else workers
    args.workspace.mkdir(parents=True, exist_ok=True)
    total = len(selected)
    shutdown = threading.Event()
    heavy_slots = threading.Semaphore(max(1, heavy_workers))

    suite_start = time.perf_counter()
    _log(
        f"Starting benchmark: {total} target(s)"
        f" (workers={workers}, heavy_workers={heavy_workers}, repeat={repeat}, warm={args.warm}, refresh={args.refresh})"
    )
    if workers > heavy_workers and has_heavy_targets:
        _log(
            f"Heavy-target concurrency capped at {heavy_workers}; use --max-heavy-workers to tune"
        )

    results_by_id: dict[str, dict] = {}
    interrupted = False

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bench") as executor:
        future_to_target: dict[Future[dict], BenchmarkTarget] = {
            executor.submit(
                run_target,
                target,
                args.workspace,
                repeat,
                args.warm,
                args.refresh,
                args.verbose,
                shutdown,
                heavy_slots,
            ): target
            for target in selected
        }
        _log(f"Submitted {len(future_to_target)} task(s) across {workers} worker(s)")

        try:
            for future in as_completed(future_to_target):
                target = future_to_target[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = _cancelled_result(target, _iso())
                    result["status"] = f"error: {exc}"
                results_by_id[target.id] = result
                _log(
                    f"[{len(results_by_id)}/{total}] {target.id} done"
                    f" | {result['status']}"
                    f" | cold_avg={format_seconds_with_unit(result['cold_avg_s'])}"
                    f" | warm={format_seconds_with_unit(result['warm_s'])}"
                    f" | modules={result['modules']}"
                )
        except KeyboardInterrupt:
            interrupted = True
            _log(
                f"Interrupt received -- stopping after in-flight tasks complete"
                f" ({len(results_by_id)}/{total} done so far)..."
            )
            shutdown.set()
            for fut in future_to_target:
                fut.cancel()
            # Drain tasks already running so they observe the shutdown event.
            for future in as_completed(future_to_target):
                target = future_to_target[future]
                if target.id in results_by_id:
                    continue
                try:
                    result = future.result()
                except Exception:
                    result = _cancelled_result(target, _iso())
                results_by_id[target.id] = result
            # Mark targets that never started.
            for target in selected:
                if target.id not in results_by_id:
                    results_by_id[target.id] = _cancelled_result(target, _iso())

    suite_elapsed = time.perf_counter() - suite_start
    status_tag = " (interrupted)" if interrupted else ""
    _log(f"Benchmark finished in {suite_elapsed:.2f}s total{status_tag}")

    # Restore original suite order.
    results = [results_by_id[t.id] for t in selected if t.id in results_by_id]

    if args.verbose:
        _log("Summary")
        for line in _table_lines(results):
            _log(line)
    else:
        print()
        print("Summary", flush=True)
        print_table(results)

    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    failed = [r["id"] for r in results if r["status"] != "ok"]
    if failed:
        _log(f"{len(failed)} target(s) did not produce ok output: {', '.join(failed)}", stderr=True)
    return 1 if interrupted or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
