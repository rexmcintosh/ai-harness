from diem.queue import Item, QueueDir, new_item

NOW = "2026-07-03T21:00:00"

def _q(tmp_path):
    return QueueDir(tmp_path / "state")

def test_roundtrip_json():
    it = new_item("ask", {"question": "q?", "panel": "decision"}, created=NOW)
    back = Item.from_json(it.to_json())
    assert back == it and back.attempts == 0 and back.max_attempts == 2

def test_add_and_pending_priority_order(tmp_path):
    q = _q(tmp_path)
    q.add(new_item("backfill", {"max_targets": 2}, created=NOW))
    q.add(new_item("review", {"repo": "/r/a", "diff": True}, created=NOW))
    q.add(new_item("images", {"repo": "/r/b", "count": 5}, created=NOW))
    banked = new_item("review", {"repo": "/r/c", "diff": True}, banked=True, created=NOW)
    q.add(banked)
    types = [(i.banked, i.type) for i in q.pending(NOW)]
    assert types == [(True, "review"), (False, "images"), (False, "review"),
                     (False, "backfill")]

def test_dedupe_same_key_rejected(tmp_path):
    q = _q(tmp_path)
    assert q.add(new_item("review", {"repo": "/r/a", "diff": True}, created=NOW))
    assert not q.add(new_item("review", {"repo": "/r/a", "diff": True}, created=NOW))
    # different repo is a different key
    assert q.add(new_item("review", {"repo": "/r/b", "diff": True}, created=NOW))

def test_expired_items_are_archived_not_returned(tmp_path):
    q = _q(tmp_path)
    q.add(new_item("ask", {"question": "old", "panel": "decision"},
                   created="2026-07-01T10:00:00", expires="2026-07-02T00:00:00"))
    assert q.pending(NOW) == []
    assert q.night_count("ask", "2026-07-01T00:00:00") == 1  # archived, still counted
    assert q.night_count("ask", "2026-07-03T01:00:00") == 0  # outside window

def test_archived_keys_since_and_requeue(tmp_path):
    q = _q(tmp_path)
    it = new_item("review", {"repo": "/r/a", "diff": True}, created=NOW)
    q.add(it)
    q.archive(it, {"ok": True})
    assert it.dedupe_key() in q.archived_keys_since("2026-07-03T01:00:00")
    assert q.archived_keys_since("2026-07-04T01:00:00") == set()
    it2 = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW)
    q.add(it2)
    it2.attempts = 1
    q.requeue(it2)
    assert q.pending(NOW)[0].attempts == 1

def test_archive_and_remove(tmp_path):
    q = _q(tmp_path)
    it = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW)
    q.add(it)
    q.archive(it, {"ok": True})
    assert q.pending(NOW) == []
    it2 = new_item("ask", {"question": "q2", "panel": "decision"}, created=NOW)
    q.add(it2)
    assert q.remove(it2.id) and q.pending(NOW) == []

def test_night_helpers_tolerate_non_dict_json(tmp_path):
    q = _q(tmp_path)
    (q.qdir / "junk.json").write_text("null")
    (q.adir / "junk2.json").write_text('"just a string"')
    assert q.night_count("ask", "2026-07-01T00:00:00") == 0
    assert q.archived_keys_since("2026-07-01T00:00:00") == set()


def test_not_before_roundtrips_and_old_files_without_it_still_load():
    it = new_item("ask", {"question": "q?", "panel": "decision"}, created=NOW, not_before="23:00")
    assert Item.from_json(it.to_json()).not_before == "23:00"
    import json
    old = json.loads(it.to_json()); old.pop("not_before")
    assert Item.from_json(json.dumps(old)).not_before is None
