import textwrap
from pathlib import Path
import pytest
from diem.config import DiemConfig, load_venice_key, load_venice_admin_key

TOML = textwrap.dedent("""
    daily_diem = 100.0
    repos = ["/home/dev/projects/swimtrack"]
    deadline = "00:50"
    reset = "01:00"
    state_dir = "{state}"
    outputs_dir = "{out}"
    loom_repo = "/home/dev/projects/ai-harness"
    loom_cmd = ["/home/dev/projects/ai-harness/.venv/bin/python", "-m", "loom.cli", "backfill"]

    [[checkpoints]]
    time = "21:00"
    floor = 0.40
    [[checkpoints]]
    time = "23:00"
    floor = 0.15
    [[checkpoints]]
    time = "00:15"
    floor = 0.0

    [seeds.images]
    cost = 2.0
    duration_s = 180
    [seeds.ask]
    cost = 0.5
    duration_s = 120

    [telegram]
    bot_token = "tok"
    chat_id = "123"

    [cmd_whitelist.teasers]
    repo = "/home/dev/projects/romance-empire"
    argv = ["python", "scripts/make_teasers.py"]
""")

def _write_cfg(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(TOML.format(state=tmp_path / "state", out=tmp_path / "out"))
    return p

def test_load_parses_all_sections(tmp_path):
    cfg = DiemConfig.load(_write_cfg(tmp_path))
    assert cfg.daily_diem == 100.0
    assert cfg.checkpoints[0].time == "21:00" and cfg.checkpoints[0].floor == 0.40
    assert cfg.checkpoints[2].floor == 0.0
    assert cfg.repos == [Path("/home/dev/projects/swimtrack")]
    assert cfg.seeds["images"]["cost"] == 2.0
    assert cfg.telegram["chat_id"] == "123"
    assert cfg.cmd_whitelist["teasers"]["argv"][0] == "python"
    assert cfg.state_dir == tmp_path / "state"

def test_load_defaults(tmp_path):
    p = tmp_path / "min.toml"
    p.write_text('daily_diem = 50.0\nrepos = []\n')
    cfg = DiemConfig.load(p)
    assert cfg.deadline == "00:50" and cfg.reset == "01:00"
    assert cfg.telegram is None
    assert cfg.backfill_max_per_night == 4 and cfg.backfill_chunk == 2
    assert cfg.state_dir == Path.home() / ".local/state/diem"

def test_load_missing_daily_diem_exits(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("repos = []\n")
    with pytest.raises(SystemExit):
        DiemConfig.load(p)

def test_daily_diem_zero_exits_2(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("daily_diem = 0\nrepos = []\n")
    with pytest.raises(SystemExit) as exc:
        DiemConfig.load(p)
    assert exc.value.code == 2

@pytest.mark.parametrize("line", [
    'VENICE_API_KEY=sk-abc123',
    'VENICE_KEY="sk-abc123"',
    "export VENICE_API_KEY='sk-abc123'",
])
def test_load_venice_key_variants(tmp_path, line):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=x\n{line}\n")
    assert load_venice_key(env) == "sk-abc123"

def test_load_venice_key_missing_exits(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=x\n")
    with pytest.raises(SystemExit):
        load_venice_key(env)

def test_load_admin_key_reads_venice_admin_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text('VENICE_ADMIN_KEY="sk-admin-xyz"\nVENICE_API_KEY=sk-inf\n')
    assert load_venice_admin_key(env) == "sk-admin-xyz"

def test_load_admin_key_missing_exits_2(tmp_path):
    env = tmp_path / ".env"; env.write_text("VENICE_API_KEY=sk-inf\n")
    with pytest.raises(SystemExit):
        load_venice_admin_key(env)

def test_partial_seed_override_keeps_default_fields(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('daily_diem = 1.0\nrepos = []\n[seeds.images]\ncost = 5.0\n')
    cfg = DiemConfig.load(p)
    assert cfg.seeds["images"] == {"cost": 5.0, "duration_s": 180}

def test_tilde_expanded_in_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / "cfg.toml"
    p.write_text(
        'daily_diem = 10.0\n'
        'repos = ["~/projects/foo"]\n'
        'state_dir = "~/state"\n'
        'outputs_dir = "~/out"\n'
        'loom_repo = "~/loom"\n'
    )
    cfg = DiemConfig.load(p)
    assert cfg.repos == [tmp_path / "projects/foo"]
    assert cfg.state_dir == tmp_path / "state"
    assert cfg.outputs_dir == tmp_path / "out"
    assert cfg.loom_repo == tmp_path / "loom"

def test_empty_checkpoints_exits_2(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('daily_diem = 1.0\nrepos = []\ncheckpoints = []\n')
    with pytest.raises(SystemExit) as exc:
        DiemConfig.load(p)
    assert exc.value.code == 2

def test_deadline_not_before_reset_exits_2(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('daily_diem = 1.0\nrepos = []\ndeadline = "01:30"\nreset = "01:00"\n')
    with pytest.raises(SystemExit) as exc:
        DiemConfig.load(p)
    assert exc.value.code == 2

def test_midnight_reset_config_loads(tmp_path):
    # 00:00 UTC epoch: deadline 23:50 sits the evening before reset 00:00 — a valid
    # 10-minute lead even though "23:50" > "00:00" as strings.
    p = tmp_path / "utc.toml"
    p.write_text('daily_diem = 31.0\nrepos = []\ndeadline = "23:50"\nreset = "00:00"\n'
                 '[[checkpoints]]\ntime = "21:00"\nfloor = 0.40\n'
                 '[[checkpoints]]\ntime = "23:45"\nfloor = 0.0\n')
    cfg = DiemConfig.load(p)
    assert cfg.reset == "00:00" and cfg.deadline == "23:50"

@pytest.mark.parametrize("bad_time", ["25:00", "10:60", "notatime", "1", "1:2:3"])
def test_bad_time_string_exits_2(tmp_path, bad_time):
    p = tmp_path / "bad.toml"
    p.write_text(f'daily_diem = 1.0\nrepos = []\ndeadline = "{bad_time}"\n')
    with pytest.raises(SystemExit) as exc:
        DiemConfig.load(p)
    assert exc.value.code == 2


def test_reserve_section_is_read_and_is_off_by_default(tmp_path):
    from diem.config import DiemConfig
    p = tmp_path / "c.toml"
    p.write_text('daily_diem = 31.0\n')
    cfg = DiemConfig.load(p)
    assert cfg.reserve_lock is None and cfg.reserve_diem == 0.0
    p.write_text('daily_diem = 31.0\n[reserve]\nlock = "~/projects/.backlog-run/lock"\ndiem = 2.0\n')
    cfg = DiemConfig.load(p)
    assert str(cfg.reserve_lock).endswith("/projects/.backlog-run/lock") and "~" not in str(cfg.reserve_lock)
    assert cfg.reserve_diem == 2.0


def test_a_negative_reserve_is_refused(tmp_path):
    import pytest
    from diem.config import DiemConfig
    p = tmp_path / "c.toml"
    p.write_text('daily_diem = 31.0\n[reserve]\nlock = "/tmp/x"\ndiem = -1\n')
    with pytest.raises(SystemExit):
        DiemConfig.load(p)


@pytest.mark.parametrize("bad", ["nan", "inf", '"two"'])
def test_a_reserve_that_is_not_a_finite_number_is_a_config_error(tmp_path, bad):
    from diem.config import DiemConfig
    p = tmp_path / "c.toml"
    p.write_text(f'daily_diem = 31.0\n[reserve]\nlock = "/tmp/x"\ndiem = {bad}\n')
    with pytest.raises(SystemExit):
        DiemConfig.load(p)
