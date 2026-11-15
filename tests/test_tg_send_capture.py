"""tg-send copies every message into the alert-watch inbox before it sends.

The copy is best effort: it must never change the send, the exit code or stdout.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

from tests.test_tg_send import SCRIPT, fake_tools

ACCEPTED = '{"ok":true,"result":{"message_id":91,"chat":{"id":123}}}\n200'
REJECTED = '{"ok":false,"error_code":400,"description":"Bad Request"}\n200'


def _watch_config(tmp_path):
    conf = tmp_path / "watch.toml"
    conf.write_text('chat_id = "123"\nwatch_chats = ["123"]\n')
    return conf


def _env(tmp_path, *responses, inbox=None, token=True, **extra):
    bindir, calls = fake_tools(tmp_path)
    env = os.environ.copy()
    env.pop("ALERT_SOURCE", None)
    env.update({
        "PATH": f"{bindir}:{env['PATH']}",
        "FAKE_CURL_CALLS": str(calls),
        "TG_SEND_RECEIPT_OUTPUT": "1",
        "ALERT_INBOX_DIR": str(inbox if inbox is not None else tmp_path / "inbox"),
        "TG_ENV": str(tmp_path / "missing.env"),
        "ALERT_WATCH_CONFIG": str(_watch_config(tmp_path)),
    })
    if token:
        env["TELEGRAM_BOT_TOKEN"] = "test-token-not-secret"
    else:
        env.pop("TELEGRAM_BOT_TOKEN", None)
    for i, response in enumerate(responses, 1):
        env[f"FAKE_CURL_{i}"] = response
    env.update(extra)
    return env, calls


def _send(env, text="hello", argv=None):
    return subprocess.run(argv or [str(SCRIPT), "123", text], env=env, text=True,
                          capture_output=True, timeout=60)


def _entries(inbox: Path):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(inbox.glob("*.json"))]


def test_capture_writes_one_complete_file_before_send(tmp_path):
    env, _ = _env(tmp_path, ACCEPTED)
    result = _send(env)
    assert result.returncode == 0
    inbox = tmp_path / "inbox"
    files = sorted(inbox.glob("*.json"))
    assert len(files) == 1
    assert not list(inbox.glob(".tmp-*"))
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert entry["version"] == 1
    assert entry["text"] == "hello"
    assert entry["chat_id"] == "123"
    assert entry["attempts"] == 0
    assert entry["captured_at"].endswith("Z")
    assert entry["id"] == files[0].stem
    assert entry["cwd"]


def test_capture_happens_even_when_send_is_rejected(tmp_path):
    env, _ = _env(tmp_path, REJECTED)
    result = _send(env)
    assert result.returncode == 1
    assert len(_entries(tmp_path / "inbox")) == 1


def test_capture_happens_without_a_token(tmp_path):
    env, calls = _env(tmp_path, token=False)
    result = _send(env)
    assert result.returncode == 1
    assert not calls.exists()
    assert [e["text"] for e in _entries(tmp_path / "inbox")] == ["hello"]


def test_alert_source_env_wins(tmp_path):
    env, _ = _env(tmp_path, ACCEPTED, ALERT_SOURCE="alert-watch")
    _send(env)
    assert _entries(tmp_path / "inbox")[0]["source"] == "alert-watch"


def test_source_names_the_calling_script(tmp_path):
    job = tmp_path / "fake-job.sh"
    job.write_text(f"#!/usr/bin/env bash\n'{SCRIPT}' 123 'from the job'\n")
    job.chmod(0o755)
    env, _ = _env(tmp_path, ACCEPTED)
    result = _send(env, argv=[str(job)])
    assert result.returncode == 0
    entry = _entries(tmp_path / "inbox")[0]
    assert "fake-job.sh" in entry["source"]
    assert entry["text"] == "from the job"


def test_capture_off(tmp_path):
    env, _ = _env(tmp_path, ACCEPTED, inbox="off")
    result = _send(env)
    assert result.returncode == 0
    assert not (tmp_path / "off").exists()
    assert not (tmp_path / "inbox").exists()


def test_capture_error_leaves_send_unchanged(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    env, calls = _env(tmp_path, ACCEPTED, inbox=blocker)
    result = _send(env)
    assert result.returncode == 0
    assert json.loads(result.stdout)["message_ids"] == [91]
    assert len(calls.read_text().splitlines()) == 1
    lines = [l for l in result.stderr.splitlines() if "capture" in l]
    assert len(lines) == 1


def test_long_emoji_message_captured_once_whole(tmp_path):
    text = "⚠️ " + "x" * 9000
    env, calls = _env(tmp_path, ACCEPTED, ACCEPTED, ACCEPTED)
    result = _send(env, text=text)
    assert result.returncode == 0
    assert len(calls.read_text().splitlines()) >= 2
    entries = _entries(tmp_path / "inbox")
    assert len(entries) == 1
    assert entries[0]["text"] == text


def test_text_from_stdin_is_captured(tmp_path):
    env, _ = _env(tmp_path, ACCEPTED)
    result = subprocess.run([str(SCRIPT), "123", "-"], env=env, input="line one\nline two",
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0
    assert _entries(tmp_path / "inbox")[0]["text"] == "line one\nline two"


def test_inbox_modes(tmp_path):
    env, _ = _env(tmp_path, ACCEPTED)
    _send(env)
    inbox = tmp_path / "inbox"
    assert stat.S_IMODE(inbox.stat().st_mode) == 0o700
    (f,) = inbox.glob("*.json")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_two_messages_get_two_files(tmp_path):
    env, _ = _env(tmp_path, ACCEPTED, ACCEPTED)
    assert _send(env, text="one").returncode == 0
    assert _send(env, text="two").returncode == 0
    assert sorted(e["text"] for e in _entries(tmp_path / "inbox")) == ["one", "two"]


def test_source_keeps_names_but_never_argument_values(tmp_path):
    job = tmp_path / "fake-job.sh"
    job.write_text(f"#!/usr/bin/env bash\n'{SCRIPT}' 123 'from the job'\n")
    job.chmod(0o755)
    env, _ = _env(tmp_path, ACCEPTED)
    result = _send(env, argv=["bash", str(job), "--token", "SECRET-abc123", "chat=999"])
    assert result.returncode == 0
    source = _entries(tmp_path / "inbox")[0]["source"]
    assert "fake-job.sh" in source
    assert "SECRET" not in source and "abc123" not in source and "999" not in source


def test_source_from_a_cron_style_sh_c_line(tmp_path):
    job = tmp_path / "coverage_check.py"
    job.write_text(f"import subprocess\nsubprocess.run([{str(SCRIPT)!r}, '123', 'gap'])\n")
    env, _ = _env(tmp_path, ACCEPTED)
    line = f"cd {tmp_path} && TOKEN=hidden python3 coverage_check.py --key s3cr3t >> log 2>&1"
    result = _send(env, argv=["/bin/sh", "-c", line])
    assert result.returncode == 0
    source = _entries(tmp_path / "inbox")[0]["source"]
    assert source.startswith("python3 coverage_check.py < sh coverage_check.py")
    assert "hidden" not in source and "s3cr3t" not in source


def test_a_message_to_an_unwatched_chat_is_not_captured(tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text('chat_id = "1"\nwatch_chats = ["1"]\n')
    env, _ = _env(tmp_path, ACCEPTED, ACCEPTED, ALERT_WATCH_CONFIG=str(conf))
    assert subprocess.run([str(SCRIPT), "999", "private"], env=env, text=True,
                          capture_output=True, timeout=60).returncode == 0
    assert not (tmp_path / "inbox").exists() or not list((tmp_path / "inbox").glob("*.json"))
    assert subprocess.run([str(SCRIPT), "1", "watched"], env=env, text=True,
                          capture_output=True, timeout=60).returncode == 0
    assert [e["text"] for e in _entries(tmp_path / "inbox")] == ["watched"]


def test_the_real_config_watches_the_chat_the_senders_use():
    import tomllib
    raw = tomllib.loads((SCRIPT.parent.parent / "alertwatch" / "config.toml").read_text())
    assert raw["watch_chats"] == ["7735693897"]
