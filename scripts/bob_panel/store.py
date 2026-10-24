"""A small JSON list store shared by several processes (the panel, the agent's announcer, Claude Code).

Every read-modify-write runs under an exclusive fcntl lock on a sidecar file, and the JSON is replaced
atomically through a per-process/thread tmp name, so concurrent writers never lose or resurrect entries.
The path is always passed in: this module owns no file of its own.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(_lock_path(path), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict], path: Path) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update(fn: Callable[[list[dict]], list[dict]], path: Path) -> list[dict]:
    """Apply ``fn`` to the current entries under the lock and save its result."""
    with locked(path):
        items = fn(load(path))
        _save(items, path)
        return items
