"""Structural regression guard: 6 subprocess-launched scripts must import logts.

These scripts are launched as separate processes via subprocess.run/Popen(
[sys.executable, ...]) and therefore do not inherit cli.py's own `import logts`
side effect (CET-prefixed stdout/stderr). Each must import logts itself.

Pure text check, no mocking -- cheap and catches accidental removal.
"""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_TARGET_FILES = [
    _REPO_ROOT / "scripts" / "teams_agent" / "stats.py",
    _REPO_ROOT / "scripts" / "teams_agent" / "sharepoint_poller.py",
    _REPO_ROOT / "scripts" / "teams_agent" / "login.py",
    _REPO_ROOT / "scripts" / "broadcast" / "broadcast_runner.py",
]


@pytest.mark.parametrize("path", _TARGET_FILES, ids=lambda p: p.name)
def test_script_imports_logts(path):
    """Each targeted script contains an `import logts` line."""
    assert path.exists(), f"expected file not found: {path}"
    content = path.read_text(encoding="utf-8")
    assert "import logts" in content, f"{path.name} is missing 'import logts'"
