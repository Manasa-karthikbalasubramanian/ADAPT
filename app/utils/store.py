"""Tiny thread-safe JSONL store (append / read / atomic rewrite)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from app import config

_lock = threading.RLock()


def path(name: str) -> Path:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return config.STATE_DIR / name


def read_all(name: str) -> list[dict]:
    p = path(name)
    with _lock:
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:   # skip a torn/corrupt line, keep going
                continue
        return out


def append(name: str, rec: dict) -> None:
    with _lock, open(path(name), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def rewrite(name: str, recs: list[dict]) -> None:
    p = path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _lock:
        tmp.write_text("".join(json.dumps(r) + "\n" for r in recs),
                       encoding="utf-8")
        os.replace(tmp, p)


def lock() -> threading.RLock:
    return _lock
