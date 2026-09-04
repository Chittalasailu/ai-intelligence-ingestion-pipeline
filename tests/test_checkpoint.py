from src.utils.checkpoint import CheckpointStore


async def test_has_seen_false_for_new_item(tmp_path):
    store = CheckpointStore(tmp_path / "cp.sqlite")
    assert await store.has_seen("ns", "item-1") is False


async def test_mark_seen_then_has_seen_true(tmp_path):
    store = CheckpointStore(tmp_path / "cp.sqlite")
    await store.mark_seen("ns", "item-1", "2026-09-04T00:00:00Z")
    assert await store.has_seen("ns", "item-1") is True


async def test_namespaces_are_isolated(tmp_path):
    store = CheckpointStore(tmp_path / "cp.sqlite")
    await store.mark_seen("news", "item-1", "2026-09-04T00:00:00Z")
    assert await store.has_seen("jobs", "item-1") is False


async def test_all_seen_returns_full_set(tmp_path):
    store = CheckpointStore(tmp_path / "cp.sqlite")
    await store.mark_seen("ns", "a", "t")
    await store.mark_seen("ns", "b", "t")
    assert await store.all_seen("ns") == {"a", "b"}


async def test_checkpoint_survives_reopening_same_file(tmp_path):
    path = tmp_path / "cp.sqlite"
    store1 = CheckpointStore(path)
    await store1.mark_seen("ns", "item-1", "t")

    store2 = CheckpointStore(path)  # simulates a resumed crawl after a restart
    assert await store2.has_seen("ns", "item-1") is True


async def test_pagination_cursor_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path / "cp.sqlite")
    assert await store.get_cursor("ns") is None
    await store.set_cursor("ns", "page-3", "2026-09-04T00:00:00Z")
    assert await store.get_cursor("ns") == "page-3"
    await store.set_cursor("ns", "page-4", "2026-09-04T00:01:00Z")
    assert await store.get_cursor("ns") == "page-4"


async def test_content_hash_dedup(tmp_path):
    store = CheckpointStore(tmp_path / "cp.sqlite")
    assert await store.has_content_hash("hash1") is False
    await store.mark_content_hash("hash1", "news", "2026-09-04T00:00:00Z")
    assert await store.has_content_hash("hash1") is True
