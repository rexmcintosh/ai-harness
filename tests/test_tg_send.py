import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "tg-send"


def fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "curl-calls"
    curl = bindir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'call\\n' >> \"$FAKE_CURL_CALLS\"\n"
        "i=$(wc -l < \"$FAKE_CURL_CALLS\")\n"
        "key=FAKE_CURL_${i}\n"
        "printf '%s' \"${!key}\"\n"
    )
    curl.chmod(0o755)
    sleep = bindir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
    sleep.chmod(0o755)
    return bindir, calls


def run_sender(tmp_path: Path, *responses: str, receipt: bool = True, text: str = "hello"):
    bindir, calls = fake_tools(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "TELEGRAM_BOT_TOKEN": "test-token-not-secret",
            "FAKE_CURL_CALLS": str(calls),
        }
    )
    if receipt:
        env["TG_SEND_RECEIPT_OUTPUT"] = "1"
    for i, response in enumerate(responses, 1):
        env[f"FAKE_CURL_{i}"] = response
    result = subprocess.run(
        [str(SCRIPT), "123", text], env=env, text=True, capture_output=True
    )
    count = len(calls.read_text().splitlines()) if calls.exists() else 0
    return result, count


def test_http_200_with_ok_false_is_not_accepted(tmp_path):
    result, calls = run_sender(
        tmp_path,
        '{"ok":false,"error_code":400,"description":"Bad Request"}\n200',
    )
    assert result.returncode == 1
    assert calls == 1
    assert "accepted" not in result.stdout


def test_accepted_response_returns_provider_receipt(tmp_path):
    result, calls = run_sender(
        tmp_path,
        '{"ok":true,"result":{"message_id":91,"chat":{"id":123}}}\n200',
    )
    assert result.returncode == 0
    assert calls == 1
    receipt = json.loads(result.stdout)
    assert receipt == {
        "ok": True,
        "status": "accepted",
        "chat_id": 123,
        "message_ids": [91],
    }
    assert "receipt" not in result.stderr.lower()


def test_rate_limit_retries_once_with_valid_retry_after(tmp_path):
    result, calls = run_sender(
        tmp_path,
        '{"ok":false,"error_code":429,"description":"Too Many Requests",'
        '"parameters":{"retry_after":0}}\n200',
        '{"ok":true,"result":{"message_id":92,"chat":{"id":123}}}\n200',
    )
    assert result.returncode == 0
    assert calls == 2


def test_network_or_unparseable_result_is_uncertain_and_not_retried(tmp_path):
    result, calls = run_sender(tmp_path, "\n000")
    assert result.returncode == 3
    assert calls == 1
    assert "uncertain" in result.stderr.lower()



def test_exhausted_rate_limit_uses_documented_definite_rejection(tmp_path):
    rate = '{"ok":false,"error_code":429,"parameters":{"retry_after":0}}\n200'
    result, calls = run_sender(tmp_path, rate, rate)
    assert result.returncode == 1 and calls == 2


def test_partial_message_failure_is_uncertain_and_not_wholly_rejected(tmp_path):
    result, calls = run_sender(tmp_path,
        '{"ok":true,"result":{"message_id":91}}\n200',
        '{"ok":false,"error_code":400}\n200', text='x' * 4100)
    assert result.returncode == 3 and calls == 2
    assert 'partial delivery' in result.stderr


def test_rate_limit_delay_is_parsed_before_deciding_to_retry(tmp_path):
    result, calls = run_sender(tmp_path,
        '{"ok":false,"error_code":429,"parameters":{"retry_after":90}}\n200')
    assert result.returncode == 1 and calls == 1
