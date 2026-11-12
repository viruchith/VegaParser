"""Pytest configuration shared by all test suites."""

# The evaluation corpus is sample source code, not VegaParser's own tests.
# Its test_*.py files import packages that only exist inside the corpus.
collect_ignore_glob = ["eval_corpus/*"]
