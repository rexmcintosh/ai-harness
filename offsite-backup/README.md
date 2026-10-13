# Backblaze B2 + restic off-site backup

Daily encrypted backup of `/home/dev` on `bebop-vm`, the Linux VM hosted by the
Mac mini. The first B2 upload and remote recovery check are required before
activation. Installing the files does not start or enable the timer.

## Scope

Includes projects, assets, `.git` and worktrees, Claude/Codex histories and settings,
secrets, SSH keys, the wiki, local scripts, and preserved migration archives.
Online SQLite databases are copied through Python's SQLite backup API and checked
with `PRAGMA quick_check`. Originals and their WAL/SHM/journal files are excluded;
the consistent copies appear at `.local/state/restic-offsite/sqlite/<relative-path>`
in each snapshot, with a `manifest.json` list. Frozen `.codex/recovery-backups`
and `migration-archive` are included directly and are not duplicated into staging.

Excludes `node_modules`, `.venv`, `venv`, `__pycache__`, `.cache`, `.npm`, `.rustup`,
`.local/share/pipx/venvs`, `.cargo/registry`, and the runner's own machine-bound
`.config/restic-offsite` credentials, which live in the Bitwarden recovery record. Check these exclusions before
placing unique files inside those directories. Backups preserve links but do not
follow them. Data outside `/home/dev`, remote Supabase/Postgres databases, macOS
files, Lima's disk image, and system configuration require separate recovery.
This is a data backup, not a bootable image. Recreate the Linux VM and install
its tools before restoring. Other files are copied while services run, so this
is not one atomic filesystem snapshot.

The timer runs daily at 03:30 Europe/Lisbon, with up to ten minutes of jitter.
`Persistent=true` catches a missed run after startup. Keep 30 daily snapshots and
six monthly snapshots, grouped by the stable machine-specific host and tag. Configuration is bound to
the Linux machine ID to prevent reuse from mixing different machines. These count days and
months with successful snapshots, not exact calendar-age limits. Each complete
backup must pass repository metadata checks and an exact-snapshot recovery-probe
restore before retention runs. Failed or partial uploads never run retention.
Prune runs after successful Sunday backups to reclaim unused data. An exclusive
local lock prevents overlapping staging work. Only online database copies are
staged locally. Backups and probe restores use `--no-cache` to limit local disk
usage. Repository checks use a fresh private temporary metadata cache, then
remove it, so repeated trees do not download the same pack repeatedly. No cache
is retained between checks. Allow space for that temporary metadata as well.

## B2 account setup

1. Enable B2 on the Backblaze account. For a new account, choose the EU region.
2. Create a dedicated **private** bucket, for example `rex-bebop-offsite-<unique>`.
   Record the bucket's S3 endpoint, such as `s3.eu-central-003.backblazeb2.com`.
3. Set Lifecycle Settings to **Keep only the last version of the file**. Restic
   handles snapshot retention; this rule removes hidden superseded B2 versions
   after one day. Do not set an upload-age rule that hides current objects.
4. Leave Object Lock/default retention disabled for this setup, so restic can
   remove expired snapshots and prune unused data.
5. Under Application Keys, create `bebop-restic`, scoped to this bucket with
   **Read and Write** access. Select **Allow List All Bucket Names** for S3
   compatibility. Leave the filename prefix empty. Use an application key,
   not the master account key.
6. Save the endpoint, bucket, keyID, applicationKey and a new random restic
   password of at least 20 characters in Bitwarden. The applicationKey is shown
   only once. This password is separate from the Mac login password. Keep this
   recovery record accessible if the mini and VM are lost.

References: [Backblaze restic setup](https://www.backblaze.com/docs/cloud-storage-integrate-restic-with-backblaze-b2),
[Backblaze lifecycle rules](https://www.backblaze.com/docs/cloud-storage-lifecycle-rules),
[restic retention](https://restic.readthedocs.io/en/stable/060_forget.html).

## Install and configure

Restic is installed from Ubuntu's package repository. From this directory:

```sh
./install.sh
```

From the Mac terminal, enter the secrets through hidden prompts. They are saved
in a mode-600 file within a mode-700 directory and are not put in shell history:

```sh
ssh -t dev@bebop-vm '~/.local/bin/restic-offsite configure'
```

Do not paste secrets into chat. This command only saves configuration. After it
finishes, the agent can initialize the repository, upload and test the first
backup without another secret handoff. The configuration is deliberately not
replaced if it already exists.

## First backup and activation

Run on `bebop-vm` as `dev`. The initial upload can take time. Use systemd for the
upload so it survives an SSH disconnect. First initialize the new repository:

```sh
~/.local/bin/restic-offsite init
systemctl --user start --no-block restic-offsite.service
journalctl --user -u restic-offsite.service -f
```

When the service has finished, confirm `restic-offsite status` reports `ok` and
`verified: true`, and the service exited with status 0. Then run the initial
remote data check and a database restore test before enabling the timer:

```sh
(
  restic_check_cache=$(mktemp -d) || exit 1
  trap 'rm -rf "$restic_check_cache"' EXIT
  ~/.local/bin/restic-offsite restic check --no-cache=false --cache-dir "$restic_check_cache" --with-cache --read-data-subset=1%
)
```

For a database restore, obtain the successful snapshot ID from `status`, dump
`/home/dev/.local/state/restic-offsite/sqlite/manifest.json`, choose a listed DB,
and dump its staged path into a private temporary file. Open the restored file
with Python sqlite3 and run `PRAGMA quick_check`; expect `ok`. Remove that
scratch copy after the test. Never restore over a running database.

Only after those checks succeed:

```sh
systemctl --user enable --now restic-offsite.timer
systemctl --user list-timers restic-offsite.timer --no-pager
```

## Check and repair

```sh
~/.local/bin/restic-offsite status
systemctl --user status restic-offsite.service --no-pager
journalctl --user -u restic-offsite.service -n 60 --no-pager
~/.local/bin/restic-offsite restic snapshots --tag bebop-offsite
```

A status of `failed`, a failed unit, or no new successful snapshot for 36 hours
means protection needs attention. `last-error.json` preserves historical stderr from captured commands with nonzero exits,
with configured credentials removed and without recording restored file contents.
Its presence does not indicate current failure; use status and timestamps. Timeouts
and diagnostic write failures still fail the operation visibly. Status records backup ID, times, database
count and verification result. `last-success.json` separately preserves the last
verified snapshot and probe across failed later runs. No notification channel is added by this setup;
use these checks to verify health. Repository structure and a recovery-probe
restore are tested on each run; this does not read every stored data pack.
Run periodic `restic check --read-data` when a full integrity audit is needed.
The private config permits `sqlite_timeout` (seconds per DB, default 600) and
`restic_timeout` (seconds per command, default 36000). If increasing those, also
increase the service `TimeoutStartSec` beyond the total expected run time.

If a partial snapshot exists, diagnose unreadable/changing files and rerun.
Never bypass the failed-backup check or remove a repository lock while a backup
is running. A failed backup leaves the timer's service visibly failed and cleans
only its own SQLite staging directory. The runner needs free space for roughly
twice the active SQLite file and WAL sizes plus a 512 MiB reserve. The recovery record
must live outside this machine. A bucket-scoped read/write key can also delete
backups if the `dev` account is compromised. This setup protects against machine
loss; it does not promise immutable recovery after an account compromise. Encrypted backups of local secrets do not replace
that record.

## Recover on a replacement VM

Install restic and this runner; configure it from Bitwarden against the **same**
repository. Do not run `init` on an existing repository. Restore into an empty
scratch directory first:

```sh
~/.local/bin/restic-offsite restic snapshots --tag bebop-offsite
~/.local/bin/restic-offsite restic restore SNAPSHOT_ID --target /path/with/enough/space --exclude /home/dev/.config/restic-offsite
```

Preserve the replacement machine's newly configured `.config/restic-offsite`
directory when copying the restored home. Do not replace it with the old machine's
configuration; its machine-ID binding will reject the replacement VM.

Restored home files are under `<target>/home/dev`. Stop each database's consumer.
For every relative path listed in the restored SQLite `manifest.json`, copy
`<target>/home/dev/.local/state/restic-offsite/sqlite/<relative-path>` to the same
path under the restored home, remove only that database's `-wal`, `-shm` and
`-journal` companions, and verify SQLite integrity before starting its consumer.
Check file ownership and permissions, recreate excluded dependencies, and test
service behavior before replacing the working home. Use the snapshot's database
copies rather than any earlier raw database archive.

## Verification

```sh
python3 -m unittest discover -s offsite-backup -v  # from repo root
```

The tests use a temporary encrypted restic repository and a live WAL database.
They verify actual recovery of settings, unpushed Git work, assets and committed
SQLite data; exclusion of dependencies and raw active DB files; overlap refusal;
and failure without retention for restic's partial-backup exit code 3. They use
no cloud credentials or live source data.

## Setup evidence, 2026-10-05

Restic 0.16.4 from Ubuntu was installed on the arm64 VM. Nine focused tests passed during initial preparation.
A read-only online-copy rehearsal against the real home copied and checked 74
SQLite databases in 31 seconds, using 433,470,791 bytes of staging. The copies
were removed afterward. No production files were restored or changed.

The full repository suite reported 2,153 passes, one skip, and three existing
failures, also reproduced on unchanged main: `test_backfill_reads_recent_outputs_once`
and both real-ledger price coverage tests in `tests/venice_usage/test_price_coverage.py`.
The system Python test invocation lacked Flask; the complete run used the existing
repository virtual environment. These failures do not involve backup code.

B2 credentials, the first remote upload, remote data check/database restore, and
timer activation were pending at initial preparation. See the current setup evidence below.

## Account setup, 2026-10-06

B2 credentials are configured. The destination is the private bucket
`rex-bebop-offsite` in `us-east-005`. Its lifecycle removes hidden old versions
after one day; Object Lock is disabled. The service credential was narrowed to
this bucket. The account-wide recovery credential in Bitwarden remains valid.
An extra bucket created by mistakenly supplying the intended bucket's ID was
removed along with the two newly created repository objects.

The first upload transferred about 21.3 GB, then B2 rejected further uploads
with `storage cap exceeded`. The Daily Storage Cap was increased to $0.05.
This is at most about $1.50 of storage charges per 30 days before tax, not a
limit on other charge categories or a guaranteed bill. Existing uploaded packs
were indexed and reused. A later service interruption preceded global memory
exhaustion; the user service manager was restarted, Cockpit and the session
bridge were checked active, and the backup was retried without discarding data.

[Backblaze cap instructions](https://www.backblaze.com/docs/en/cloud-storage-create-and-manage-caps-and-alerts).

## Active backup and recovery evidence, 2026-10-06

The first complete snapshot is `8cf9f7d8d7f0fda1956f722826249b60d56f613c1ca5ffdf288ec73c5bb2a7ec`.
It contains 75 consistent SQLite copies. The backup service completed with exit
status 0 after repository metadata verification, an exact-snapshot probe restore,
and retention. The separate cloud audit checked 1% of stored data packs (20 packs)
and found no errors. Four files totaling about 282.6 MiB were restored into a
private scratch directory: settings, the installed runner, the repository's
working agreement, and a staged Codex history database. Every SHA-256 matched
the pre-upload source. The restored database passed `PRAGMA quick_check`.
Scratch files and the temporary audit cache were removed.

A no-cache repository metadata check took about twelve minutes. The same check
with a fresh temporary metadata cache completed in about one second. Automated
verification now uses that private per-check cache and removes it on normal
success or failure. SIGKILL or power loss can leave temporary files behind.
Backups and probe restores continue without a persistent cache.

Thirteen focused backup tests pass, including cache cleanup after verification
success and failure. The full suite reports 2,157 passes, one skip, and the same
three unrelated failures listed above. Council approved the production changes;
its requested cache flag test coverage was added and the focused suite rerun.

The daily timer is enabled and active. At activation, its next run was
2026-10-07 03:36:21 Europe/Lisbon, within the configured 03:30 plus ten-minute
jitter window. An additional incremental backup was already running at activation.
The verified snapshot remains recoverable independently of that later run.
Private commissioning evidence is in `~/.local/state/restic-offsite/`, including
`last-success.json`, `cloud-recovery-test.json`, test logs and review records.
Use the health checks above for current status rather than this dated record.

## Review fixes, 2026-10-13

Seven scheduled runs from 2026-10-07 to 2026-10-13 each finished with exit status 0
and a passed recovery check. Review then found two reliability gaps, now fixed:

- `restic backup` runs with `--json --quiet`, so its output is only the final
  summary. Before, about 40 progress lines per second were held in memory for
  the whole upload, which reaches hundreds of thousands of lines on a daily run.
- The runner removes its SQLite staging before it writes `status: ok`. If that
  removal fails, the status is `failed` with `failed_stage: cleanup`, and
  `last-success.json` still names the verified snapshot.
- Captured restic stderr is drained as it arrives. Only the last 32 KiB is kept
  in memory, starting at a whole line, and it is redacted before it is saved.
- SIGTERM, SIGINT and SIGHUP, including a systemd stop or `TimeoutStartSec`
  expiry, record `status: failed` with `error_type: Terminated` and the stage
  that was running, then remove staging. An uncatchable SIGKILL or OOM kill
  leaves `running` on disk; `restic-offsite status` then records and reports
  `interrupted` when no run holds the lock. Treat `interrupted` like `failed`.
- Database copying also checks free space before and during each copy and
  stops while the 512 MiB reserve remains, because live databases can grow
  after the estimate.
- `status.json` stays `running` through verification, retention and cleanup,
  and becomes `ok` only at the end. `last-success.json` records the verified
  snapshot as soon as verification passes. Any unfinished state with no lock
  holder is reported as `interrupted`, and the next run records it under
  `previous_run_interrupted`.
- New snapshots exclude `.config/restic-offsite`. Earlier snapshots still hold
  it, so keep the `--exclude` in the recovery restore command.

- Captured stdout is capped at 1 MiB; more output fails the command. A missing
  or unreadable `status.json` is reported as `unknown`.

- `restic-offsite verify` checks the current snapshot when the latest run is
  `ok`. Otherwise it checks the snapshot in `last-success.json`, updates only
  that file, and leaves the failed or interrupted current status in place.
- `uploaded_at` marks the end of the upload. `finished_at` is set only when
  the run ends, with success or failure.

Twenty-two focused backup tests pass. Run `./install.sh` after merging so the
installed runner matches this version; the timer stays enabled.
