import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "watchdog" / "run-watchdog.sh"


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def run_wrapper(tmp_path: Path, send_rc: int):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({
        "version": 1, "attempt_id": "attempt-1", "detected_at": 1,
        "fired": [{"name": "disk", "level": "crit", "summary": "full", "evidence": ""}],
        "candidate_suppression_state": {"disk": {"level": "crit", "ts": 1}},
        "delivery": {"status": "pending"},
    }))
    precheck = executable(tmp_path / "precheck", "#!/usr/bin/env bash\nprintf 'disk full\\nWATCHDOG_JSON:{\"escalate\":true,\"attempt_id\":\"attempt-1\"}\\n'\n")
    claude = executable(tmp_path / "claude", "#!/usr/bin/env bash\nprintf '{\"result\":\"diagnosis\"}\\n'\n")
    receipt = '{"ok":true,"status":"accepted","chat_id":1,"message_ids":[9]}'
    sender = executable(tmp_path / "tg-send", f"#!/usr/bin/env bash\nprintf '{receipt}\\n'\nexit {send_rc}\n")
    env = os.environ.copy()
    env.update({
        "WATCHDOG_PRECHECK_CMD": str(precheck),
        "WATCHDOG_CLAUDE_BIN": str(claude),
        "WATCHDOG_TG_SEND": str(sender),
        "WATCHDOG_LOG_DIR": str(tmp_path / "logs"),
        "WATCHDOG_ENV_FILE": str(tmp_path / "missing-env"),
        "WATCHDOG_STATE": str(tmp_path / "state.json"),
        "WATCHDOG_PENDING": str(pending),
        "WATCHDOG_DELIVERY_LAST": str(tmp_path / "last.json"),
        "WATCHDOG_METRICS": str(tmp_path / "metrics.json"),
    })
    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
    return result, pending


def test_wrapper_commits_suppression_only_after_accepted_receipt(tmp_path):
    result, pending = run_wrapper(tmp_path, 0)
    assert result.returncode == 0
    assert not pending.exists()
    assert json.loads((tmp_path / "state.json").read_text())["disk"]["ts"] == 1
    assert json.loads((tmp_path / "last.json").read_text())["delivery"]["provider_receipt"]["message_ids"] == [9]


def test_wrapper_retains_failed_delivery_without_suppression(tmp_path):
    result, pending = run_wrapper(tmp_path, 1)
    assert result.returncode == 1
    assert not (tmp_path / "state.json").exists()
    assert json.loads(pending.read_text())["delivery"]["status"] == "failed"
