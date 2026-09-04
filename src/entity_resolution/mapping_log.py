"""Append-only CSV writer for the entity mapping log (raw name -> canonical
name, method, confidence, source, timestamp) — this is the exact "Entity
Mapping Log" tab required in the Google Sheets deliverable.
"""
from __future__ import annotations

import csv
import threading
from pathlib import Path

from src.entity_resolution.resolver import MappingLogEntry

_FIELDS = ["raw_name", "canonical_name", "method", "confidence", "source_url", "timestamp"]


class MappingLogWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen_rows: set[tuple[str, str, str]] = set()
        self.rows: list[dict] = []
        if self.path.exists():
            # Each CLI invocation (`--pipeline startups`, then `--pipeline
            # products`, ...) constructs a fresh MappingLogWriter. Without
            # loading the file's existing keys here, the same company
            # resolved by two different pipelines in two different
            # processes would pass this instance's (empty) in-memory dedup
            # check and get appended again — this is exactly what produced
            # duplicate rows in early runs (see data/mappings cleanup note
            # in docs/LIMITATIONS.md).
            with self.path.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    self._seen_rows.add((row["raw_name"], row["canonical_name"], row["method"]))
        else:
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=_FIELDS).writeheader()

    def write(self, entry: MappingLogEntry) -> None:
        key = (entry.raw_name, entry.canonical_name, entry.method)
        with self._lock:
            if key in self._seen_rows:
                return
            self._seen_rows.add(key)
            row = {
                "raw_name": entry.raw_name,
                "canonical_name": entry.canonical_name,
                "method": entry.method,
                "confidence": entry.confidence,
                "source_url": entry.source_url or "",
                "timestamp": entry.timestamp.isoformat(),
            }
            self.rows.append(row)
            with self.path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_FIELDS)
                writer.writerow(row)
