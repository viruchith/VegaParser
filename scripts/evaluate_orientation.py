#!/usr/bin/env python3
"""Offline evaluation of repository orientation. No LLM, no network.

Measures two things against hand-written ground truth:

1. Extraction - precision/recall of files, symbols, internal dependency edges
   and external URLs (``evaluation/ground_truth.json``).
2. Retrieval - for each orientation question (``evaluation/questions.json``),
   how many expected files appear in the top-k files a strategy would place in
   a context pack.

Strategies:
  full_bundle  every parsed file (what ``bundle`` includes today; no selection)
  keyword      files ranked by question-term overlap with path, symbol names
               and docstrings (a simple baseline a developer could approximate
               with ripgrep)

Run from the repository root with the project venv:

    .venv\\Scripts\\python scripts\\evaluate_orientation.py
    .venv\\Scripts\\python scripts\\evaluate_orientation.py --k 5 --json out.json
    .venv\\Scripts\\python scripts\\evaluate_orientation.py --write-docs
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from repo_parser.models import ParsedFile  # noqa: E402
from repo_parser.parser.engine import ParserEngine  # noqa: E402
from repo_parser.traversal.scanner import RepositoryScanner  # noqa: E402

EVAL_DIR = REPO_ROOT / "evaluation"
GROUND_TRUTH_PATH = EVAL_DIR / "ground_truth.json"
QUESTIONS_PATH = EVAL_DIR / "questions.json"
DOCS_PATH = REPO_ROOT / "docs" / "evaluation.md"

# Generic English function words only. Domain words such as "code" or "file"
# are deliberately kept so the baseline is not tuned to these questions.
STOPWORDS = frozenset(
    "a an and are as at be by do does for from has have how in is it of on or "
    "the to what when where which who why with that this there their its we you "
    "i me my can will should into about not no".split()
)


@dataclass(frozen=True)
class Score:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    def __add__(self, other: Score) -> Score:
        return Score(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)


def score(produced: set, expected: set) -> Score:
    return Score(
        tp=len(produced & expected),
        fp=len(produced - expected),
        fn=len(expected - produced),
    )


# ── parsing ──────────────────────────────────────────────────────────────────


def parse_repo(root: Path) -> tuple[dict[str, ParsedFile], dict[str, int]]:
    """Parse a repository the same way ``repo-parser init`` does (no cache)."""
    scanner = RepositoryScanner(root)
    engine = ParserEngine()
    parsed: list[ParsedFile] = []
    source_chars: dict[str, int] = {}
    java_sources: dict[str, str] = {}
    for rel in scanner.discover():
        content = scanner.read_file(rel)
        if content is None:
            continue
        result = engine.parse_file(rel.as_posix(), content)
        if result is not None:
            parsed.append(result)
            source_chars[result.filepath] = len(content)
            if result.filepath.endswith(".java"):
                java_sources[result.filepath] = content
    engine.infer_internal_dependencies(parsed, java_sources)
    return {pf.filepath: pf for pf in parsed}, source_chars


def _reportable(name: str) -> bool:
    """Dunder methods (``__init__``) are excluded from symbol scoring."""
    return not (name.startswith("__") and name.endswith("__"))


def symbol_keys(pf: ParsedFile) -> set[str]:
    """Return ``path::Name`` / ``path::Class.method`` keys for a parsed file."""
    keys: set[str] = set()
    for cls in pf.classes:
        if _reportable(cls.name):
            keys.add(f"{pf.filepath}::{cls.name}")
        for method in cls.methods:
            # Constructors (Java) share the class name; treat them as the class.
            if _reportable(method.name) and method.name != cls.name:
                keys.add(f"{pf.filepath}::{cls.name}.{method.name}")
    for fn in pf.functions:
        if fn.is_method and fn.parent_class:
            if _reportable(fn.name) and fn.name != fn.parent_class:
                keys.add(f"{pf.filepath}::{fn.parent_class}.{fn.name}")
        elif _reportable(fn.name):
            keys.add(f"{pf.filepath}::{fn.name}")
    return keys


# ── extraction scoring ───────────────────────────────────────────────────────


def extraction_scores(parsed: dict[str, ParsedFile], truth: dict) -> dict[str, Score]:
    produced_files = set(parsed)
    produced_symbols: set[str] = set()
    produced_deps: set[tuple[str, str]] = set()
    produced_urls: set[tuple[str, str]] = set()
    for pf in parsed.values():
        produced_symbols |= symbol_keys(pf)
        produced_deps |= {(pf.filepath, dep) for dep in pf.internal_dependencies}
        produced_urls |= {(pf.filepath, u.url) for u in pf.external_urls}

    return {
        "files": score(produced_files, set(truth["files"])),
        "symbols": score(produced_symbols, set(truth["symbols"])),
        "dependencies": score(produced_deps, {tuple(e) for e in truth["dependencies"]}),
        "urls": score(produced_urls, {tuple(e) for e in truth["urls"]}),
    }


# ── retrieval ────────────────────────────────────────────────────────────────


def _terms(text: str) -> set[str]:
    """Lowercase word terms with camelCase split and a naive plural stem."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    terms = set()
    for token in re.findall(r"[a-z0-9]+", spaced.lower()):
        if token in STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        terms.add(token)
    return terms


def _document_terms(path: str, pf: ParsedFile) -> set[str]:
    text_parts = [path, pf.module_docstring or ""]
    for cls in pf.classes:
        text_parts += [cls.name, cls.docstring or ""]
        text_parts += [m.name for m in cls.methods]
    for fn in pf.functions:
        text_parts += [fn.name, fn.docstring or ""]
    terms: set[str] = set()
    for part in text_parts:
        terms |= _terms(part)
    return terms


def keyword_pack(question: str, parsed: dict[str, ParsedFile], k: int) -> list[str]:
    query = _terms(question)
    ranked: list[tuple[int, str]] = []
    for path, pf in parsed.items():
        overlap = len(query & _document_terms(path, pf))
        if overlap:
            ranked.append((-overlap, path))
    return [path for _, path in sorted(ranked)][:k]


def full_bundle_pack(parsed: dict[str, ParsedFile]) -> list[str]:
    return sorted(parsed)


@dataclass
class RetrievalResult:
    question_id: str
    repo: str
    strategy: str
    pack: list[str]
    recall: float
    precision: float
    hit: bool
    pack_chars: int


def evaluate_retrieval(
    questions: list[dict],
    repos: dict[str, tuple[dict[str, ParsedFile], dict[str, int]]],
    k: int,
) -> list[RetrievalResult]:
    results: list[RetrievalResult] = []
    for q in questions:
        parsed, source_chars = repos[q["repo"]]
        expected = set(q["expected_files"])
        strategies = {
            "full_bundle": full_bundle_pack(parsed),
            "keyword": keyword_pack(q["question"], parsed, k),
        }
        for name, pack in strategies.items():
            hits = expected & set(pack)
            results.append(
                RetrievalResult(
                    question_id=q["id"],
                    repo=q["repo"],
                    strategy=name,
                    pack=pack,
                    recall=len(hits) / len(expected),
                    precision=len(hits) / len(pack) if pack else 0.0,
                    hit=bool(hits),
                    pack_chars=sum(source_chars.get(p, 0) for p in pack),
                )
            )
    return results


def summarise_retrieval(results: list[RetrievalResult]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for strategy in sorted({r.strategy for r in results}):
        rows = [r for r in results if r.strategy == strategy]
        n = len(rows)
        summary[strategy] = {
            "questions": n,
            "mean_recall": sum(r.recall for r in rows) / n,
            "mean_precision": sum(r.precision for r in rows) / n,
            "hit_rate": sum(r.hit for r in rows) / n,
            "mean_pack_files": sum(len(r.pack) for r in rows) / n,
            "mean_pack_chars": sum(r.pack_chars for r in rows) / n,
        }
    return summary


# ── reporting ────────────────────────────────────────────────────────────────


def run_evaluation(k: int) -> dict:
    truth_all = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))

    repos: dict[str, tuple[dict[str, ParsedFile], dict[str, int]]] = {}
    extraction: dict[str, dict[str, Score]] = {}
    for name, truth in truth_all.items():
        root = (REPO_ROOT / truth["root"]).resolve()
        repos[name] = parse_repo(root)
        extraction[name] = extraction_scores(repos[name][0], truth)

    overall: dict[str, Score] = {}
    for per_repo in extraction.values():
        for category, s in per_repo.items():
            overall[category] = overall[category] + s if category in overall else s

    retrieval = evaluate_retrieval(questions, repos, k)
    return {
        "k": k,
        "environment": {"python": platform.python_version(), "os": platform.system()},
        "repos": {name: {"files_parsed": len(repos[name][0])} for name in repos},
        "extraction": {
            name: {cat: {"tp": s.tp, "fp": s.fp, "fn": s.fn, "precision": s.precision, "recall": s.recall}
                   for cat, s in per_repo.items()}
            for name, per_repo in extraction.items()
        },
        "extraction_overall": {
            cat: {"tp": s.tp, "fp": s.fp, "fn": s.fn, "precision": s.precision, "recall": s.recall}
            for cat, s in overall.items()
        },
        "retrieval_summary": summarise_retrieval(retrieval),
        "retrieval": [r.__dict__ for r in retrieval],
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(results: dict) -> str:
    k = results["k"]
    lines = [
        "## Results",
        "",
        f"Measured on Python {results['environment']['python']} ({results['environment']['os']}). "
        f"Retrieval uses k={k}. Generated by `scripts/evaluate_orientation.py`.",
        "",
        "### Extraction (overall, all three corpus repos)",
        "",
        "| Category | TP | FP | FN | Precision | Recall |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cat, s in results["extraction_overall"].items():
        lines.append(
            f"| {cat} | {s['tp']} | {s['fp']} | {s['fn']} | {_pct(s['precision'])} | {_pct(s['recall'])} |"
        )

    lines += ["", "### Extraction per repo", "", "| Repo | Category | TP | FP | FN | Precision | Recall |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for repo, cats in results["extraction"].items():
        for cat, s in cats.items():
            lines.append(
                f"| {repo} | {cat} | {s['tp']} | {s['fp']} | {s['fn']} | {_pct(s['precision'])} | {_pct(s['recall'])} |"
            )

    lines += ["", f"### Retrieval (mean over questions, k={k})", "",
              "| Strategy | Questions | Recall | Precision | Hit rate | Mean pack files | Mean pack source chars |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for strategy, s in results["retrieval_summary"].items():
        lines.append(
            f"| {strategy} | {s['questions']} | {_pct(s['mean_recall'])} | {_pct(s['mean_precision'])} | "
            f"{_pct(s['hit_rate'])} | {s['mean_pack_files']:.1f} | {s['mean_pack_chars']:,.0f} |"
        )

    lines += ["", "### Retrieval per question", "",
              "| Question | Repo | Expected files | Keyword recall | Keyword precision | Keyword pack |",
              "|---|---|---:|---:|---:|---|"]
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    by_key = {(r["question_id"], r["strategy"]): r for r in results["retrieval"]}
    for q in questions:
        r = by_key[(q["id"], "keyword")]
        pack = ", ".join(f"`{p}`" for p in r["pack"]) or "(none)"
        lines.append(
            f"| {q['id']}: {q['question']} | {q['repo']} | {len(q['expected_files'])} | "
            f"{_pct(r['recall'])} | {_pct(r['precision'])} | {pack} |"
        )
    return "\n".join(lines) + "\n"


GENERATED_BEGIN = "<!-- BEGIN GENERATED RESULTS -->"
GENERATED_END = "<!-- END GENERATED RESULTS -->"


def write_docs(markdown: str) -> None:
    """Replace only the generated block in docs/evaluation.md; narrative is kept."""
    existing = DOCS_PATH.read_text(encoding="utf-8")
    if GENERATED_BEGIN not in existing or GENERATED_END not in existing:
        raise SystemExit(f"{DOCS_PATH} is missing the generated-results markers")
    head, _, rest = existing.partition(GENERATED_BEGIN)
    _, _, tail = rest.partition(GENERATED_END)
    DOCS_PATH.write_text(f"{head}{GENERATED_BEGIN}\n{markdown}{GENERATED_END}{tail}", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--k", type=int, default=5, help="Pack size for top-k retrieval (default: 5).")
    parser.add_argument("--json", type=Path, help="Write full results as JSON to this path.")
    parser.add_argument(
        "--write-docs",
        action="store_true",
        help="Replace the generated block in docs/evaluation.md with these results.",
    )
    args = parser.parse_args()

    results = run_evaluation(args.k)
    markdown = render_markdown(results)
    print(markdown)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    if args.write_docs:
        write_docs(markdown)
        print(f"Updated {DOCS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
