# diem — DIEM drain engine

## What it is

Nightly Venice DIEM drain: converts unspent daily allowance into standing workload automatically and deterministically. At 21:00, 23:00, and 00:15 local time, cron fires `diem drain --checkpoint`, which pops jobs from a queue and runs them until the balance drops to the floor or the deadline (00:50) approaches. The drain never implements workloads itself — it shells out to trusted tooling: `council review`, `council ask`, `loom backfill`, and repo-provided image pipelines. See [design doc](../docs/superpowers/specs/2026-07-03-diem-drain-engine-design.md).

Diem owns the queue, the clock, and the budget only. Every output sits behind human gates: reviews land as staging files, images append to candidate dirs, loom writes to its own store. It never commits, pushes, merges, publishes, or touches KDP. The operator banks items during the day (or Claude sessions write them as JSON files), and the drain executes them at night, always yielding to live interactive use.

## Config

Create `~/.config/diem/config.toml`. Required key: `daily_diem` (your daily DIEM allowance in units). All others optional; defaults shown.

```toml
daily_diem = 50  # REQUIRED — your daily DIEM allowance
repos = ["/path/to/repo1", "/path/to/repo2"]
deadline = "00:50"  # Must fall between last checkpoint and reset
reset = "01:00"
state_dir = "~/.local/state/diem"
outputs_dir = "~/.local/state/diem/outputs"
backfill_max_per_night = 4
backfill_chunk = 2

[[checkpoints]]
time = "21:00"
floor = 0.40  # Drain if balance exceeds 40% of daily_diem

[[checkpoints]]
time = "23:00"
floor = 0.15

[[checkpoints]]
time = "00:15"
floor = 0.0

[seeds]
ask = {cost = 0.5, duration_s = 120}
review = {cost = 1.0, duration_s = 180}
images = {cost = 2.0, duration_s = 180}
backfill = {cost = 1.0, duration_s = 300}
cmd = {cost = 1.0, duration_s = 300}

[telegram]
bot_token = "..."
chat_id = "..."

[cmd_whitelist.teasers]
repo = "/home/dev/projects/romance-empire"
argv = ["python", "scripts/make_teasers.py"]
```

Config contract: `deadline` must fall between the last checkpoint time and `reset` on the clock (e.g., 00:50 < 01:00). `daily_diem` is required; all others inherit sensible defaults.

## Queue-file banking

One JSON file per item in `~/.local/state/diem/queue/`. Claude sessions (or humans via `diem queue add`) write files directly; `diem` reads and executes them. Schema:

```json
{
  "id": "unique-ulid",
  "type": "ask|review|images|backfill|cmd",
  "banked": true,
  "priority": 100,
  "payload": {"question": "...", "panel": "decision"},
  "created": "2026-07-03T21:00:00",
  "expires": "2026-07-10T21:00:00 or null",
  "attempts": 0,
  "max_attempts": 2
}
```

Banked items always outrank discovered items. Deduped by type-specific key: one `review` per repo per night, one `images` per standing order per night. Stale items with an `expires` timestamp die quietly instead of burning DIEM. Type payloads: `ask` = {question, panel}; `review` = {repo, range? | diff?}; `images` = {repo, count}; `backfill` = {max_targets}; `cmd` = {name}.

## Leftovers-only work: `not_before`

An item may carry `"not_before": "HH:MM"`, a time of the DIEM day (anchored to the day start
like the checkpoints, so `23:00` is still due at 00:20 under a 01:00 reset). Before that time
the drain does not see the item at all: it cannot take the morning allowance, it is not
reported as skipped, and it does not stop the loom filler from seeding (the filler needs an
empty queue). Use it for work that is worth doing only with DIEM that would expire anyway:
`diem queue add ask "..." --not-before 23:00` (the command refuses a value that is not HH:MM;
in a hand-written queue file a value that does not parse counts as absent).

## Reserve while the night runner works

`backlog-run work` fires at 22:00 UTC and its council reviews land until about midnight. The
floor-0 slot would empty the balance under it, and a review on an empty balance is a failed
review. With

```toml
[reserve]
lock = "~/projects/.backlog-run/lock"   # the runner's flock
diem = 2.0                              # two reviews
```

the drain leaves `diem` unspent while some process holds that flock. It asks again before
every job, so the reserve ends the moment the runner lets go; the higher of floor and
reserve wins. The check reads `/proc/locks` and never takes the lock itself: a probe that
took it, even briefly, could make the runner's own non-blocking attempt fail and cost it the
night. No `[reserve]` section or a lock file that does not exist means no reserve. A probe
that cannot look (`/proc/locks` unreadable) KEEPS the reserve: wrong that way costs a little
expired DIEM, wrong the other way costs a failed review. Each checkpoint record in `drain.log`
carries `"reserve"` (the largest seen in that slot) and `"reserve_probe"`
(`off`, `held`, `not_held` or `probe_error`, the last answer). The match is the ext4 identity
(hex major:minor, decimal inode); on overlay or btrfs check it before relying on it.

## Crontab installation

Three checkpoints run `diem drain --checkpoint` and log to `~/.local/state/diem/drain.log`. Append to your crontab:

```cron
0 21 * * *  /home/dev/.local/bin/diem drain --checkpoint >> /home/dev/.local/state/diem/drain.log 2>&1
0 23 * * *  /home/dev/.local/bin/diem drain --checkpoint >> /home/dev/.local/state/diem/drain.log 2>&1
15 0 * * *  /home/dev/.local/bin/diem drain --checkpoint >> /home/dev/.local/state/diem/drain.log 2>&1
```

(Install via `crontab -e` after explicit operator approval only.)

## Operations

**Status:** `diem status` anytime — shows balance, floor for now, hours to reset, queue depth, all pending items (banked marked `B`).

**Pause/resume:** `diem pause` (until next reset); `diem pause 2h` (2 hours); `diem resume` to clear the pause marker.

**Queue management:**
```bash
diem queue add ask "Reply with PONG" --panel decision
diem queue add review /path/to/repo --range main..feature
diem queue add images /path/to/repo 3
diem queue add backfill --max-targets 2
diem queue add cmd teasers
diem queue list
diem queue rm <id>
```

Output destinations: review reports → `~/.local/state/diem/outputs/reviews/`; ask answers → `~/.local/state/diem/outputs/asks/`; logs → `~/.local/state/diem/outputs/logs/`; morning report → `~/.local/state/diem/reports/YYYY-MM-DD.md`; drain summary → `~/.local/state/diem/summaries/YYYY-MM-DD.jsonl`.

## Scheduling & semantics

| Time  | Floor | Meaning |
|-------|-------|---------|
| 21:00 | 40%   | Drain surplus; operator may still be working |
| 23:00 | 15%   | Probably done; drain most of it |
| 00:15 | 0%    | Use-it-or-lose-it endgame |

Drain loop: read live balance; if ≤ floor, stop. Pop job; skip if estimated cost would breach floor or if `now + duration > 00:50` (deadline). Run job (hard timeout at deadline), archive, loop. Diem does not itself retry or back off on Venice 429/5xx — `council` and `loom` own their own request-level retry/backoff. Diem's retry granularity is one checkpoint: a failed job is requeued and tried again at the next checkpoint (once; second failure archives it as failed). Never drains blind: if the balance endpoint is unreachable, the whole checkpoint aborts (`balance_unavailable`) rather than guessing. Aborts with `past_deadline` if `now > 00:50`. Aborts with `no_checkpoint_fired` on off-schedule runs (enforces 21:00/23:00/00:15 only). One review per repo per night. Backfill capped at `backfill_max_per_night` jobs; `backfill_chunk` controls targets per job. Balance re-read between jobs — live operator use automatically throttles the drain; `diem pause` quiets it explicitly.

## Safety gates

The drain is **read-and-stage only**. It never commits, pushes, merges, publishes, or touches KDP. Review findings land as report files; image candidates append to the repo's candidates dir; loom writes to its own store. All output sits behind the existing human gates. `cmd` runs only whitelisted commands keyed by repo. Diem does not talk to Venice for images and does not inspect Venice response headers itself — the target repo's own standing-order pipeline owns image generation, `x-venice-is-content-violation` handling, and quarantine. Diem only counts files already staged in the standing order's `candidates_dir` against its `target` and, on a shortfall, queues an `images` job asking that pipeline to make up the difference; it never stages images itself. `images` queue items carry only `repo` and `count` — the command to run is never accepted from the queue payload. At run time the runner resolves argv SOLELY from the target repo's `.diem/standing-order.json` (checked again, since the file may have changed since discovery time); no standing order (or a malformed one) → no images job. This closes off a queue-dir writer smuggling arbitrary argv past the advertised whitelist/standing-order gate. This boundary keeps diem out of creative direction; open decisions (e.g., object-row direction) remain the operator's.
