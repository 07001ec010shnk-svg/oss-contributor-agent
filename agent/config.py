from __future__ import annotations
import json
import threading
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]

class LiveConfig:
    """Read a replacement env atomically on next use, without process restart."""
    def __init__(self, path: Path | None = None):
        self.path = Path(path or ROOT / "api_keys.env")
        self._lock = threading.RLock()
        self._signature = None
        self._values = {}

    def snapshot(self) -> dict:
        with self._lock:
            try:
                stat = self.path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                return dict(self._values)
            if signature != self._signature:
                try:
                    parsed = {k: v for k, v in dotenv_values(self.path).items() if v is not None}
                    if parsed:
                        self._values = parsed
                        self._signature = signature
                except (OSError, UnicodeError, ValueError):
                    pass  # keep previous valid configuration during an incomplete save
            return dict(self._values)

def settings() -> dict:
    path = ROOT / "settings.json"
    return json.loads(path.read_text(encoding="utf-8"))

def as_int(value, default=0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
