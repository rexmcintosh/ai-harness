# DIEM checkpoint drain

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `diem drain --checkpoint`

## Purpose and authority

**Default mode:** bounded change-producing work. At four UTC checkpoints, select eligible queued DIEM work within the configured allowance and deadline, run it, append a checkpoint summary, and send the configured evening or completion notice. Local cron documentation and host configuration confirm UTC scheduling. Canonical queue, pause, estimates, review, and summary state are under the configured DIEM state directory, selected by `~/.config/diem/config.toml`.

It may execute queued work under DIEM’s own queue policy. When the queue is truly empty, the observed implementation may seed a bounded configured Loom `backfill` item, then subjects it to the same budget and deadline checks. It must not bypass a pause, exceed the configured allowance, or merge or deploy unless the selected item’s own authority allows it.

**Secrets, names only:** `VENICE_API_KEY` or `VENICE_KEY`, `VENICE_ADMIN_KEY`, `TELEGRAM_BOT_TOKEN`, plus configured per-project `VENICE_*` keys.

## Success and evidence

Success is a checkpoint JSON summary appended to `summaries/<UTC-day>.jsonl`, with each run item marked `ok`, and the cron exit recorded in `~/.local/state/diem/drain.log`. Inspect the configured state directory and the log. A nonempty queue is not itself a failure because the allowance and deadline may defer work.

## Failure, escalation, and gaps

Failed items remain visible in the summary. The completion report attempts Telegram notification but catches reporting errors, so local summaries are the durable evidence. The contract does not yet prove that queued-item authority is checked for every runner type.
