from pathlib import Path

from scout.adapters.local.fs_store import FsStore


def test_list_works_with_relative_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the default config uses a relative ".forge-data"
    store = FsStore(Path(".forge-data"))
    store.put_json("bounties/a.json", {"x": 1})
    assert store.list("bounties") == ["bounties/a.json"]
    assert store.get_json("bounties/a.json") == {"x": 1}
