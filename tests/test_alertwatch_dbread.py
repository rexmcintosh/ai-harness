import json

from alertwatch import dbread


def fake(rows):
    calls = []

    def select(table, filters, cols):
        calls.append((table, filters, cols))
        return rows
    return select, calls


def test_reads_and_prints_json_lines(capsys):
    select, calls = fake([{"sr_meet_id": "11539", "poller_attempts": 1}])
    assert dbread.main(["meet_registry", "sr_meet_id=eq.11539", "--select", "sr_meet_id,poller_attempts"],
                       select=select) == 0
    assert calls == [("meet_registry", "sr_meet_id=eq.11539&limit=200", "sr_meet_id,poller_attempts")]
    assert json.loads(capsys.readouterr().out) == {"sr_meet_id": "11539", "poller_attempts": 1}


def test_no_filters(capsys):
    select, calls = fake([])
    assert dbread.main(["meets", "--limit", "5"], select=select) == 0
    assert calls == [("meets", "limit=5", "*")]


def test_bad_inputs_refused():
    select, calls = fake([])
    assert dbread.main(["meets; drop"], select=select) == 2
    assert dbread.main(["meets", "--select", "a b"], select=select) == 2
    assert dbread.main(["meets", "--limit", "5000"], select=select) == 2
    assert dbread.main(["meets", "id=eq.1&limit=9999"], select=select) == 2
    assert calls == []


def test_full_page_warns(capsys):
    select, _ = fake([{"id": i} for i in range(3)])
    dbread.main(["meets", "--limit", "3"], select=select)
    assert "there may be more" in capsys.readouterr().err


def test_filters_are_postgrest_pairs_only():
    select, calls = fake([])
    assert dbread.main(["meets", "id=eq.1&select=secret"], select=select) == 2
    assert dbread.main(["meets", "on_conflict=id"], select=select) == 2
    assert dbread.main(["meets", "id=eq.1#frag"], select=select) == 2
    assert dbread.main(["meets", "not a filter"], select=select) == 2
    assert calls == []
    assert dbread.main(["meets", "status=eq.live&order=start_date.desc&or=(a.eq.1,b.eq.2)"], select=select) == 0
