from tests.test_tg_send import run_sender


def test_success_stays_silent_for_existing_callers_unless_receipt_is_requested(tmp_path, monkeypatch):
    monkeypatch.delenv('TG_SEND_RECEIPT_OUTPUT', raising=False)
    result, _ = run_sender(tmp_path, '{"ok":true,"result":{"message_id":91,"chat":{"id":123}}}\n200', receipt=False)
    assert result.returncode == 0
    assert result.stdout == ''
