"""The package version and pyproject.toml must agree."""

import re
from pathlib import Path

from repo_parser import __version__

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"


def test_package_version_matches_pyproject():
    match = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "pyproject.toml has no version"
    assert __version__ == match.group(1)
