"""Monkey-patch sys.stdout and sys.stderr to prefix each line with HH:MM:SS CET.

Import once in the entry point — all modules in the process inherit it.
Both streams are patched so plain `print(...)` and `print(..., file=sys.stderr)`
get the same timestamping; previously only stderr was patched, leading to
mixed-format console output.
"""
from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

_CET = ZoneInfo("Europe/Warsaw")


class _Timestamped:
    def __init__(self, stream):
        self._s = stream
        self._bol = True  # beginning-of-line

    def write(self, data: str) -> int:
        if not data:
            return 0
        if self._bol:
            ts = datetime.now(_CET).strftime("%H:%M:%S")
            data = f"{ts} {data}"
        self._bol = data.endswith("\n")
        return self._s.write(data)

    def flush(self) -> None:
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


sys.stdout = _Timestamped(sys.stdout)
sys.stderr = _Timestamped(sys.stderr)
