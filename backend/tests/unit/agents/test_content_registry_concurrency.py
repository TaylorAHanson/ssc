"""Concurrent content saves never expose a half-written file to readers.

``PUT /content/{filename}`` and ``GET /content/{filename}`` run concurrently in
the threadpool. A truncate-then-write save let a reader see an empty/partial
file, which ``get_content`` silently returns as ``{}``.
"""

import json
import threading

from app.agents import content_registry


def test_readers_never_see_partial_content_during_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(content_registry, "CONTENT_DIR", tmp_path)
    big = {"items": [{"title": f"link {i}", "url": f"https://example.com/{i}" * 20} for i in range(300)]}
    content_registry.save_content("links.json", big, create_version=False)

    empty_reads = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            if content_registry.get_content("links.json") == {}:
                empty_reads.append(1)

    def writer(n):
        for i in range(25):
            payload = dict(big, rev=f"{n}-{i}")
            assert content_registry.save_content("links.json", payload, create_version=False)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert not empty_reads, f"{len(empty_reads)} reads saw a truncated file"
    final = json.loads((tmp_path / "links.json").read_text())
    assert len(final["items"]) == 300
    assert not list(tmp_path.glob("*.tmp")), "temp files must not be left behind"


def test_save_keeps_file_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(content_registry, "CONTENT_DIR", tmp_path)
    content_registry.save_content("perm.json", {"a": 1}, create_version=False)
    path = tmp_path / "perm.json"
    path.chmod(0o644)

    content_registry.save_content("perm.json", {"a": 2}, create_version=False)

    assert path.stat().st_mode & 0o777 == 0o644
    assert json.loads(path.read_text()) == {"a": 2}


def test_version_backup_still_created(tmp_path, monkeypatch):
    monkeypatch.setattr(content_registry, "CONTENT_DIR", tmp_path)
    content_registry.save_content("v.json", {"a": 1}, create_version=False)

    content_registry.save_content("v.json", {"a": 2})

    backups = [p for p in tmp_path.glob("v-*.json")]
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == {"a": 1}
