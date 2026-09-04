import csv

from src.entity_resolution.mapping_log import MappingLogWriter
from src.entity_resolution.resolver import EntityResolver, MappingLogEntry


def _entry(raw="OpenAI Inc", source="https://example.com/a"):
    r = EntityResolver().resolve(raw)
    return MappingLogEntry.build(raw, r, source)


def test_write_creates_file_with_header(tmp_path):
    path = tmp_path / "log.csv"
    MappingLogWriter(path)
    with path.open(encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["raw_name", "canonical_name", "method", "confidence", "source_url", "timestamp"]


def test_write_appends_row(tmp_path):
    writer = MappingLogWriter(tmp_path / "log.csv")
    writer.write(_entry())
    assert len(writer.rows) == 1
    with (tmp_path / "log.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "OpenAI"


def test_write_dedups_within_same_instance(tmp_path):
    writer = MappingLogWriter(tmp_path / "log.csv")
    writer.write(_entry())
    writer.write(_entry())  # identical raw/canonical/method
    assert len(writer.rows) == 1


def test_write_dedups_across_process_restarts(tmp_path):
    # Regression test: caught by actually running startups.py then
    # products.py as two separate CLI invocations against the same YC
    # company list — the same company got written twice because a fresh
    # MappingLogWriter per process didn't know about the other process's
    # rows already on disk.
    path = tmp_path / "log.csv"
    first_run = MappingLogWriter(path)
    first_run.write(_entry())

    second_run = MappingLogWriter(path)  # simulates a new CLI invocation
    second_run.write(_entry())  # same raw/canonical/method as first_run wrote

    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert len(second_run.rows) == 0  # this instance recognized it as already-logged


def test_write_allows_different_entries_across_restarts(tmp_path):
    path = tmp_path / "log.csv"
    first_run = MappingLogWriter(path)
    first_run.write(_entry(raw="OpenAI Inc"))

    second_run = MappingLogWriter(path)
    second_run.write(_entry(raw="Anthropic PBC", source="https://example.com/b"))

    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
