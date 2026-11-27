"""run-expect.sh picks the MeetTrack bot only when its token file exists."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "watchdog" / "run-expect.sh"


def _bot_lines() -> str:
    text = SCRIPT.read_text()
    start = text.index('MEETTRACK_TG_ENV="${MEETTRACK_TG_ENV')
    end = text.index("fi\n", start) + 3
    return text[start:end]


def _run(tmp_path, bot_file):
    script = tmp_path / "probe.sh"
    script.write_text(_bot_lines() + 'echo "${TG_ENV:-none} ${TELEGRAM_BOT_TOKEN:-none}"\n')
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "MEETTRACK_TG_ENV": str(bot_file),
           "TG_ENV": "/inherited.env", "TELEGRAM_BOT_TOKEN": "inherited"}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True).stdout.split()


def test_bot_file_present_selects_it(tmp_path):
    bot = tmp_path / "telegram.env"
    bot.write_text("TELEGRAM_BOT_TOKEN=x\n")
    assert _run(tmp_path, bot) == [str(bot), "none"]


def test_bot_file_missing_changes_nothing(tmp_path):
    assert _run(tmp_path, tmp_path / "missing.env") == ["/inherited.env", "inherited"]
