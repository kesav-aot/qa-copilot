"""Append-only audit log.

Records which identity was used for what, and every policy decision. Entries are
scrubbed on the way in, so the audit trail is safe to ship to a SIEM.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qa_copilot.sanitize import sanitizer

_lock = threading.Lock()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> dict:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "pid": os.getpid(),
            **sanitizer.scrub(fields),
        }
        line = json.dumps(record, default=str)
        with _lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
