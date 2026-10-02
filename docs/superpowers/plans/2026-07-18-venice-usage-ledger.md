# Venice Usage Ledger + Reporter (Secrets Phase 2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Venice API call on mesh-vps a passive, append-only SQLite usage row (project × task_type × model × tokens × usd), and a reporter that rolls it up and reconciles it against Venice's authoritative per-key billing.

**Architecture:** A new stdlib-only `venice_usage` package in the `council` monorepo owns the SQLite ledger at `~/.local/state/venice-usage/ledger.db` and a `venice-usage` console script (`log` = append one row; `report` = offline rollup). `venice-usage log` is the **universal append primitive**: in-monorepo Python imports `venice_usage.append()` directly; external projects (romance, swimtrack×3) and Node/shell/curl call-sites shell out to the CLI. Every append is best-effort — a logging failure can never break the Venice call. Reconciliation lives in `diem` (`diem venice-usage`), where the Venice **admin** key belongs: it compares 7-day ledger totals per project against Venice's per-key trailing-7-day usage. No proxy, no gateway, nothing in the hot path.

**Tech Stack:** Python 3.11+ (stdlib `sqlite3`, `argparse`, `tomllib`), `requests` (already a dep, for the diem Venice client), pytest. Node 22 + shell callers shell out to the `venice-usage` CLI (they gain no new deps). Packaged via setuptools + pipx like the rest of the monorepo.

## Global Constraints

- **Python floor 3.11** (`requires-python = ">=3.11"`); stdlib only for `venice_usage` — no new third-party deps. `diem` may use `requests` (already present).
- **Never break a Venice call because logging failed.** Every append at a call-site is wrapped so any exception/timeout/missing-binary is swallowed (warn to stderr at most). The `venice-usage log` CLI itself always exits 0.
- **No secret values ever printed / logged / put on a command line.** The ledger stores no keys. Key minting/wiring is done by the user via hidden prompts.
- **Ledger DB path:** `~/.local/state/venice-usage/ledger.db`, overridable via `$VENICE_USAGE_DB` (needed for tests + isolation). XDG-state convention (sibling to `~/.local/state/diem`).
- **Timestamps are ISO-8601 UTC, second precision** (`datetime.now(timezone.utc)`), matching diem's UTC-epoch anchoring.
- **Source of truth docs** (`/home/dev/docs/SECRETS-AND-DEPLOY-MAP.md`, `secrets-map-notes-2026-07-18.md`, chmod 600, not in git) are updated as each phase lands. Reality wins over the docs.
- **Package name is `council`** (v0.4.0). New CLIs register in `[project.scripts]`; new packages register in `[tool.setuptools] packages`. Tests live under `tests/<pkg>/` with `pythonpath = ["."]`.

---

## Scope & Decisions (approve this section first)

**Confirmed with the user (2026-07-18):**
- **D1 — Instrument all code call-sites** (Python + Node + shell, incl. romance's 8 shell drafters). Hermes is **not** instrumented — it is being **retired** in parallel (unused; "remove surface area"). Reconcile will show no Hermes key, by design.
- **D2 — Romance per-phase = central CLI + keep CSV.** All romance paths (the shell drafters *and* `venice_runtime.py`) call `venice-usage log` with `task_type = <stage>`; `venice_runtime.py` keeps writing its richer per-chapter `usage-log.csv` unchanged. No per-phase keys.

**Recommended (baked into this plan — flag now if you disagree):**
- **D3 — One universal append CLI.** `venice-usage log …`. Rationale: pipx isolates the `council` venv, so external projects can't `import venice_usage`; there's no `sqlite3` CLI on the box and Node 22's SQLite is experimental. A single Python CLI on PATH is the only uniform, low-dep write path across all 3 languages. In-monorepo callers (council, loom, diem) import `venice_usage.append()` directly.
- **D4 — Reconcile source = Venice per-key trailing-7-day usage** (via `/api/v1/api_keys`), compared to the ledger's 7-day per-project totals. Venice's public docs don't expose a clean historical `/billing/usage` endpoint; the per-key trailing-7d figure (USD/VCU/DIEM) is the authoritative cross-check. The client is built defensively (like `BalanceClient`); the response shape was **confirmed live on 2026-07-19** — `data[].usage.trailingSevenDays.{usd,vcu,diem}` (as strings), keyed by `description`, with `consumptionLimits` (caps) and `currentPeriodUsage` also available. This unknown is now closed.
- **D5 — Key→project mapping by naming convention.** Reconcile maps a Venice key to a project by its `description`: a `proj-<project>` prefix is stripped, otherwise the name is used as-is. No extra config file.
  **Live reality (observed 2026-07-19, 13 keys on the account):** existing keys are named `ADMIN`, `CI`, `DEFAULT`, `romance-empire`, `Swimtrack`, `swimtrack_coach`, `SwimTrack-Images`, `AI-Council`, `Claude Code`, `GameBuilding`, `MacWhisper`, `OpenClaw`, `n8n` — none use a `proj-` prefix, and several belong to **off-box** consumers (MacWhisper/OpenClaw/n8n/Claude Code) not documented in the secrets map.
  ⚠️ **Harmonization is required for reconcile to align:** the key `description` must equal the `project` tag the ledger writes. Planned ledger tags (`romance`, `swimtrack`, `swimtrack-coach`, `swimtrack-website`, …) do **not** match today's key names (`romance-empire`, `Swimtrack`, `swimtrack_coach`). Phase D must pick one and apply it consistently — either rename the Venice keys to the ledger tags (or `proj-<tag>`), or choose ledger tags that match the key names. Off-box and non-project keys (ADMIN/CI/DEFAULT/MacWhisper/…) simply show up unmatched, which is correct and harmless.
  ✅ **DECIDED 2026-07-19 — clean tags, keys renamed to match exactly (no `proj-` prefix).** The canonical tags are the `project` column of the Phase C site table: **`romance`, `swimtrack`, `swimtrack-coach`, `swimtrack-website`, `council`, `loom`, `venice-ai-skill`**. Phase C hardcodes exactly these at the call-sites; Phase D renames/mints the Venice keys to those exact strings so `description == project` and the reconcile table lines up with no indirection.
- **D6 — diem gains `VENICE_ADMIN_KEY`.** diem does not read it today (grep-confirmed); Task B1 wires it (`load_venice_admin_key()` reading `~/.env`). The balance probe keeps using the inference key.

**Known deltas from the brief (reality wins):**
- Surface is ~12 code modules + ~9 romance shell scripts, not "~8". `council/venice.py` covers all **17** PR-review shims at once.
- The **diem balance probe** (`diem/balance.py`, a `rate_limits` GET) consumes **no tokens/USD** — it is **excluded** from the ledger (the ledger tracks spend, not rate-limit reads). Noted so "every call path" is understood precisely.

---

## File Structure

**Create (Phase A — `venice_usage` package):**
- `venice_usage/__init__.py` — re-exports `append`, `connect`, `default_db`.
- `venice_usage/ledger.py` — schema, `connect()`, `append()`, `query_rollup()`.
- `venice_usage/pricing.py` — static model→price map + `estimate_usd()`.
- `venice_usage/cli.py` — `venice-usage log` / `venice-usage report`.
- `tests/venice_usage/__init__.py`, `test_ledger.py`, `test_pricing.py`, `test_cli.py`.

**Modify (Phase A packaging):**
- `pyproject.toml` — add `venice_usage` to `packages`; add `venice-usage = "venice_usage.cli:main"` to `[project.scripts]`.

**Create/Modify (Phase B — reconciler in `diem`):**
- `diem/usage.py` (create) — `UsageClient` (Venice per-key usage) + `UsageUnavailable`.
- `diem/config.py` (modify) — extract `_read_env()`; add `load_venice_admin_key()`.
- `diem/cli.py` (modify) — `venice-usage` subparser + `_cmd_venice_usage()` + dispatch.
- `tests/diem/test_usage.py` (create), `tests/diem/test_config.py` + `test_cli.py` (extend).

**Modify (Phase C — instrumentation, one log call each):** the ~12 code modules + ~9 shell scripts enumerated in the Phase C site table.

**Ops (Phase D — no code):** per-project `.env` files + `source` shims; the two source-of-truth map docs.

---

## Phase A — Ledger core (`venice_usage`)

Foundation. Everything else depends on `venice-usage log` existing on PATH and `venice_usage.append()` being importable.

### Task A1: Ledger schema + `append()`

**Files:**
- Create: `venice_usage/ledger.py`, `venice_usage/__init__.py`
- Test: `tests/venice_usage/test_ledger.py`, `tests/venice_usage/__init__.py`

**Interfaces:**
- Produces: `append(*, project: str, task_type: str, model: str, tokens_in: int = 0, tokens_out: int = 0, usd: float | None = None, source: str | None = None, ts: str | None = None, db_path: str | Path | None = None) -> int` (returns new row id). `connect(db_path=None) -> sqlite3.Connection`. `default_db() -> Path` (resolves `$VENICE_USAGE_DB` live on each call — no import-time constant).
- If `usd is None`, `append` calls `pricing.estimate_usd(model, tokens_in, tokens_out)` (Task A2) — for Task A1, stub `estimate_usd` to `return None` so tests pass, then A2 fills it.

- [ ] **Step 1: Write the failing test**

```python
# tests/venice_usage/test_ledger.py
from venice_usage.ledger import append, connect

def test_append_writes_one_row(tmp_path):
    db = tmp_path / "l.db"
    rid = append(project="romance", task_type="draft", model="claude-opus-4-8",
                 tokens_in=1000, tokens_out=2000, usd=0.17, source="venice-draft.sh",
                 ts="2026-07-18T10:00:00", db_path=db)
    assert rid == 1
    rows = connect(db).execute(
        "SELECT project,task_type,model,tokens_in,tokens_out,usd,source,ts FROM usage").fetchall()
    assert rows == [("romance","draft","claude-opus-4-8",1000,2000,0.17,"venice-draft.sh","2026-07-18T10:00:00")]

def test_append_creates_parent_dir_and_is_append_only(tmp_path):
    db = tmp_path / "nested" / "dir" / "l.db"
    append(project="council", task_type="review", model="m", db_path=db)
    append(project="council", task_type="ask", model="m", db_path=db)
    n = connect(db).execute("SELECT count(*) FROM usage").fetchone()[0]
    assert n == 2  # append-only, both rows retained

def test_defaults_zero_tokens_and_null_usd(tmp_path):
    db = tmp_path / "l.db"
    append(project="p", task_type="t", model="m", db_path=db)
    row = connect(db).execute("SELECT tokens_in,tokens_out,usd FROM usage").fetchone()
    assert row == (0, 0, None)  # unknown price -> NULL usd (stub estimate_usd)

def test_default_db_resolved_from_env_at_call_time(tmp_path, monkeypatch):
    # db_path omitted -> connect()/append() must resolve $VENICE_USAGE_DB LIVE, not a
    # frozen import-time snapshot. Set it AFTER import to prove call-time resolution.
    target = tmp_path / "env.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(target))
    append(project="p", task_type="t", model="m")            # no db_path
    assert target.exists()
    assert connect().execute("SELECT count(*) FROM usage").fetchone()[0] == 1

def test_auto_timestamp_is_iso_utc_second_precision(tmp_path):
    import re
    db = tmp_path / "l.db"
    append(project="p", task_type="t", model="m", db_path=db)  # ts omitted -> _utcnow_iso()
    ts = connect(db).execute("SELECT ts FROM usage").fetchone()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)  # UTC, second precision, no offset
    from datetime import datetime, timezone
    got = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - got).total_seconds()) < 10  # actually UTC-now, not local

def test_default_db_ignores_empty_env(monkeypatch):
    from pathlib import Path
    from venice_usage.ledger import default_db
    monkeypatch.setenv("VENICE_USAGE_DB", "")
    assert default_db() == Path.home() / ".local/state/venice-usage/ledger.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo/worktree root): `pytest tests/venice_usage/test_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'venice_usage'`.

- [ ] **Step 3: Write minimal implementation**

```python
# venice_usage/ledger.py
from __future__ import annotations
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

def default_db() -> Path:
    # Resolved at CALL time (not import) so $VENICE_USAGE_DB set later — e.g. by a
    # test's monkeypatch.setenv — is honored. No import-time snapshot to go stale.
    # An empty (set-but-blank) value falls back to the default, not Path("") -> ".".
    env = os.environ.get("VENICE_USAGE_DB")
    if env:
        return Path(env)
    return Path.home() / ".local/state/venice-usage/ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT    NOT NULL,
  project    TEXT    NOT NULL,
  task_type  TEXT    NOT NULL,
  model      TEXT    NOT NULL,
  tokens_in  INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  usd        REAL,
  source     TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS ix_usage_proj_task ON usage(project, task_type);
"""

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()

def connect(db_path=None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else default_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn

def append(*, project, task_type, model, tokens_in=0, tokens_out=0,
           usd=None, source=None, ts=None, db_path=None) -> int:
    ts = ts or _utcnow_iso()
    if usd is None:
        from .pricing import estimate_usd
        usd = estimate_usd(model, int(tokens_in), int(tokens_out))
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO usage(ts,project,task_type,model,tokens_in,tokens_out,usd,source)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ts, project, task_type, model, int(tokens_in), int(tokens_out), usd, source))
        conn.commit()
        return cur.lastrowid
```

```python
# venice_usage/__init__.py
from .ledger import append, connect, default_db
__all__ = ["append", "connect", "default_db"]
```

```python
# venice_usage/pricing.py  (A1 stub; A2 fills it)
def estimate_usd(model, tokens_in, tokens_out):
    return None
```

```python
# tests/venice_usage/__init__.py   (empty, makes it a package)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/venice_usage/test_ledger.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add venice_usage/ tests/venice_usage/
git commit -m "feat(venice-usage): SQLite ledger schema + append()"
```

### Task A2: Pricing estimate

**Files:**
- Modify: `venice_usage/pricing.py`
- Test: `tests/venice_usage/test_pricing.py`

**Interfaces:**
- Produces: `estimate_usd(model: str, tokens_in: int, tokens_out: int) -> float | None`. Returns `None` for unknown/image models (caller passes `--usd` for images). `TEXT_PRICES: dict[str, tuple[float, float]]` = $ per 1M (input, output) tokens.

- [ ] **Step 1: Write the failing test**

```python
# tests/venice_usage/test_pricing.py
from venice_usage.pricing import estimate_usd

def test_known_text_model_priced_from_tokens():
    # claude-opus-4-8 seeded at (15.0, 75.0) $/Mtok -> 1M in + 1M out = 15 + 75
    assert estimate_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 90.0

def test_rounds_to_six_places():
    assert estimate_usd("claude-opus-4-8", 1000, 0) == round(1000/1e6*15.0, 6)

def test_unknown_model_returns_none():
    assert estimate_usd("flux-2-max", 0, 0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/venice_usage/test_pricing.py -v`
Expected: FAIL — stub returns `None`, so `test_known_text_model_priced_from_tokens` fails.

- [ ] **Step 3: Write minimal implementation**

```python
# venice_usage/pricing.py
"""Static estimate map ($/1M tokens for text). Estimates only — the diem
reconciler is the authoritative cross-check. Update as Venice pricing moves;
unknown/image models return None and the caller passes --usd."""
from __future__ import annotations

# $ per 1,000,000 tokens: (input, output). Seed values — verify vs Venice pricing.
TEXT_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "deepseek-v4-pro": (0.5, 2.0),
    "qwen3-235b-a22b-instruct-2507": (0.2, 0.6),
}

def estimate_usd(model, tokens_in, tokens_out):
    price = TEXT_PRICES.get(model)
    if price is None:
        return None
    pin, pout = price
    return round(tokens_in / 1e6 * pin + tokens_out / 1e6 * pout, 6)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/venice_usage/test_pricing.py tests/venice_usage/test_ledger.py -v`
Expected: PASS (ledger's `test_defaults_zero_tokens_and_null_usd` still passes — model `"m"` is unknown → NULL).

- [ ] **Step 5: Commit**

```bash
git add venice_usage/pricing.py tests/venice_usage/test_pricing.py
git commit -m "feat(venice-usage): static token-price estimator"
```

### Task A3: Rollup query

**Files:**
- Modify: `venice_usage/ledger.py`
- Test: `tests/venice_usage/test_ledger.py` (add)

**Interfaces:**
- Produces: `query_rollup(*, since=None, until=None, project=None, group_by=("project","task_type"), db_path=None) -> list[dict]`. Each dict: the group-by columns plus `calls: int`, `tokens_in: int`, `tokens_out: int`, `usd: float`. `since`/`until` are inclusive ISO strings compared against `ts`. `group_by` is validated against an allowlist `{project, task_type, model, source}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/venice_usage/test_ledger.py  (append)
from venice_usage.ledger import query_rollup

def _seed(db):
    append(project="romance", task_type="draft", model="claude-opus-4-8",
           tokens_in=1000, tokens_out=1000, usd=0.09, ts="2026-07-18T01:00:00", db_path=db)
    append(project="romance", task_type="edit", model="claude-sonnet-4-6",
           tokens_in=500, tokens_out=200, usd=0.01, ts="2026-07-18T02:00:00", db_path=db)
    append(project="council", task_type="review", model="claude-opus-4-8",
           tokens_in=800, tokens_out=100, usd=0.02, ts="2026-07-19T02:00:00", db_path=db)

def test_rollup_groups_and_sums(tmp_path):
    db = tmp_path / "l.db"; _seed(db)
    rows = query_rollup(group_by=("project",), db_path=db)
    by = {r["project"]: r for r in rows}
    assert by["romance"]["calls"] == 2 and by["romance"]["usd"] == 0.10
    assert by["council"]["usd"] == 0.02

def test_rollup_filters_since_until_and_project(tmp_path):
    db = tmp_path / "l.db"; _seed(db)
    rows = query_rollup(since="2026-07-18T00:00:00", until="2026-07-18T23:59:59",
                        project="romance", group_by=("task_type",), db_path=db)
    assert {r["task_type"] for r in rows} == {"draft", "edit"}

def test_rollup_rejects_bad_group_by(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        query_rollup(group_by=("project; DROP TABLE usage",), db_path=tmp_path / "l.db")

def test_rollup_rejects_empty_group_by(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        query_rollup(group_by=(), db_path=tmp_path / "l.db")

def test_rollup_rejects_bare_string_group_by(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        query_rollup(group_by="project", db_path=tmp_path / "l.db")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/venice_usage/test_ledger.py -v -k rollup`
Expected: FAIL — `ImportError: cannot import name 'query_rollup'`.

- [ ] **Step 3: Write minimal implementation**

```python
# venice_usage/ledger.py  (add)
_GROUP_COLS = {"project", "task_type", "model", "source"}

def query_rollup(*, since=None, until=None, project=None,
                 group_by=("project", "task_type"), db_path=None) -> list[dict]:
    if isinstance(group_by, str):
        raise ValueError("group_by must be a sequence of columns, not a string")
    group_by = tuple(group_by)
    if not group_by:
        raise ValueError("group_by must name at least one column")
    bad = [c for c in group_by if c not in _GROUP_COLS]
    if bad:
        raise ValueError(f"invalid group_by column(s): {bad}")
    where, params = [], []
    if since:   where.append("ts >= ?"); params.append(since)
    if until:   where.append("ts <= ?"); params.append(until)
    if project: where.append("project = ?"); params.append(project)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ", ".join(group_by)
    sql = (f"SELECT {cols}, COUNT(*) AS calls, "
           "COALESCE(SUM(tokens_in),0) AS tokens_in, "
           "COALESCE(SUM(tokens_out),0) AS tokens_out, "
           "COALESCE(SUM(usd),0.0) AS usd "
           f"FROM usage{clause} GROUP BY {cols} ORDER BY usd DESC")
    with closing(connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    # SUM() over REAL accumulates float error (0.09 + 0.01 -> 0.09999999999999999);
    # usd is money, so round each group's total to 6 places (micro-dollar) here.
    for r in rows:
        r["usd"] = round(r["usd"], 6)
    return rows
```

Also re-export it so `venice_usage.query_rollup(...)` works (the diem reconciler in B3 calls it that way):

```python
# venice_usage/__init__.py
from .ledger import append, connect, query_rollup, default_db
__all__ = ["append", "connect", "query_rollup", "default_db"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/venice_usage/test_ledger.py -v`
Expected: PASS. (`group_by` columns come from a validated allowlist — never string-formatted from user input.)

- [ ] **Step 5: Commit**

```bash
git add venice_usage/ledger.py venice_usage/__init__.py tests/venice_usage/test_ledger.py
git commit -m "feat(venice-usage): grouped rollup query"
```

### Task A4: `venice-usage` CLI (`log` + `report`)

**Files:**
- Create: `venice_usage/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/venice_usage/test_cli.py`

**Interfaces:**
- Produces: console entry `venice-usage` → `venice_usage.cli:main`.
  - `venice-usage log --project P --task-type T --model M [--tokens-in N] [--tokens-out N] [--usd U] [--source S] [--ts ISO]` — appends one row; **always exits 0** (best-effort).
  - `venice-usage report [--since D] [--until D] [--project P] [--group-by a,b] [--json]` — prints a table or JSON of `query_rollup`.
- Consumes: `append`, `query_rollup` (Tasks A1/A3).
- Tests set `$VENICE_USAGE_DB` via `monkeypatch` to an isolated tmp DB.

- [ ] **Step 1: Write the failing test**

```python
# tests/venice_usage/test_cli.py
import json
import venice_usage.cli as cli
from venice_usage.ledger import connect

def test_log_appends_row_and_exits_zero(tmp_path, monkeypatch):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    rc = cli.main(["log", "--project", "romance", "--task-type", "draft",
                   "--model", "claude-opus-4-8", "--tokens-in", "10", "--tokens-out", "20",
                   "--source", "venice-draft.sh"])
    assert rc == 0
    assert connect(db).execute("SELECT count(*) FROM usage").fetchone()[0] == 1

def test_log_never_raises_on_bad_db(tmp_path, monkeypatch, capsys):
    # unwritable path -> still exit 0, warning on stderr
    monkeypatch.setenv("VENICE_USAGE_DB", "/proc/nonexistent/l.db")
    rc = cli.main(["log", "--project", "p", "--task-type", "t", "--model", "m"])
    assert rc == 0
    assert "venice-usage" in capsys.readouterr().err

def test_report_json_rollup(tmp_path, monkeypatch, capsys):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    cli.main(["log", "--project", "romance", "--task-type", "draft", "--model", "m", "--usd", "0.20"])
    cli.main(["log", "--project", "romance", "--task-type", "edit", "--model", "m", "--usd", "0.05"])
    rc = cli.main(["report", "--group-by", "project", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["project"] == "romance" and abs(data[0]["usd"] - 0.25) < 1e-9

def test_log_never_raises_on_malformed_arguments(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    # non-int --tokens-in: argparse would sys.exit(2) inside parse_args -> log still exits 0
    rc = cli.main(["log", "--project", "p", "--task-type", "t", "--model", "m",
                   "--tokens-in", "not-a-number"])
    assert rc == 0
    assert "log failed (ignored)" in capsys.readouterr().err

def test_log_missing_required_flag_still_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    rc = cli.main(["log", "--project", "p", "--task-type", "t"])   # missing --model
    assert rc == 0
    assert "log failed (ignored)" in capsys.readouterr().err

def test_report_argparse_error_not_swallowed(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    # the log-only SystemExit guard must NOT swallow report's argparse errors
    with pytest.raises(SystemExit) as ei:
        cli.main(["report", "--nonexistent-flag"])
    assert ei.value.code == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/venice_usage/test_cli.py -v`
Expected: FAIL — `No module named 'venice_usage.cli'` (the A1 stub file has no `main`).

- [ ] **Step 3: Write minimal implementation**

```python
# venice_usage/cli.py
"""`venice-usage` — the universal append primitive + offline rollup.
`log` is best-effort and always exits 0: logging must never break a caller."""
from __future__ import annotations
import argparse
import json
import sys
from .ledger import append, query_rollup

def _cmd_log(a) -> int:
    try:
        append(project=a.project, task_type=a.task_type, model=a.model,
               tokens_in=a.tokens_in, tokens_out=a.tokens_out,
               usd=a.usd, source=a.source, ts=a.ts)
    except Exception as e:  # noqa: BLE001 — append is best-effort
        print(f"venice-usage: log failed (ignored): {e}", file=sys.stderr)
    return 0

def _cmd_report(a) -> int:
    gb = tuple(c.strip() for c in a.group_by.split(",") if c.strip())
    try:
        rows = query_rollup(since=a.since, until=a.until, project=a.project, group_by=gb)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    if a.json:
        print(json.dumps(rows, indent=1)); return 0
    if not rows:
        print("(no usage rows)"); return 0
    headers = list(gb) + ["calls", "tokens_in", "tokens_out", "usd"]
    widths = {h: max(len(h), *(len(str(r[h])) for r in rows)) for h in headers}
    print("  ".join(h.ljust(widths[h]) for h in headers))
    for r in rows:
        cells = [str(r[h]) if h != "usd" else f"{r['usd']:.4f}" for h in headers]
        print("  ".join(c.ljust(widths[h]) for c, h in zip(cells, headers)))
    return 0

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="venice-usage")
    sub = p.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log")
    lg.add_argument("--project", required=True)
    lg.add_argument("--task-type", required=True, dest="task_type")
    lg.add_argument("--model", required=True)
    lg.add_argument("--tokens-in", type=int, default=0, dest="tokens_in")
    lg.add_argument("--tokens-out", type=int, default=0, dest="tokens_out")
    lg.add_argument("--usd", type=float, default=None)
    lg.add_argument("--source", default=None)
    lg.add_argument("--ts", default=None)
    rp = sub.add_parser("report")
    rp.add_argument("--since"); rp.add_argument("--until"); rp.add_argument("--project")
    rp.add_argument("--group-by", default="project,task_type", dest="group_by")
    rp.add_argument("--json", action="store_true")
    argv = sys.argv[1:] if argv is None else list(argv)
    # `log` must exit 0 even on a MALFORMED invocation: argparse validation
    # (missing/bad flag) calls sys.exit(2) inside parse_args() — before _cmd_log's
    # try/except — which would abort a `set -e` caller. Swallow that for `log` only
    # (report keeps normal argparse/ValueError exit codes; --help, code 0, is kept).
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        if argv[:1] == ["log"] and e.code not in (0, None):
            print("venice-usage: log failed (ignored): bad arguments", file=sys.stderr)
            return 0
        raise
    return _cmd_log(a) if a.cmd == "log" else _cmd_report(a)

if __name__ == "__main__":
    raise SystemExit(main())
```

Then edit `pyproject.toml`:

```toml
[project.scripts]
council = "council.cli:main"
diem = "diem.cli:main"
session-gc = "sessiongc.cli:main"
venice-usage = "venice_usage.cli:main"

[tool.setuptools]
packages = ["council", "diem", "sessiongc", "venice_usage"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/venice_usage/ -v`
Expected: PASS (all venice_usage tests).

- [ ] **Step 5: Commit**

```bash
git add venice_usage/cli.py pyproject.toml tests/venice_usage/test_cli.py
git commit -m "feat(venice-usage): log + report CLI, register console script"
```

### Task A5: Smoke-test the entry point (PATH install deferred to merge)

**Files:** none (verification only). **Do NOT run a persistent `pipx` install from this worktree** — pipx would repoint the global `council` install at an ephemeral worktree that gets reaped, breaking the live `diem`/`council`. The real `venice-usage`-on-PATH install is a **post-merge deploy step**; on the branch, verify the entry point via module invocation.

- [ ] **Step 1: Smoke-test the console entry point via module invocation** (from the worktree root)

Run:
```bash
VENICE_USAGE_DB=/tmp/vu-smoke.db python -m venice_usage.cli log --project smoke --task-type t --model m --usd 0.01 --source manual
VENICE_USAGE_DB=/tmp/vu-smoke.db python -m venice_usage.cli report --json
```
Expected: report prints one `smoke` row with `usd 0.01`. Then `rm -f /tmp/vu-smoke.db*`.

- [ ] **Step 2: Post-merge deploy step (record, do not run on the branch)**

After this branch merges to `main`, run `pipx reinstall council` (or `pipx install "$HOME/projects/build-ai-automation-workflow" --force`) so `venice-usage` lands on `~/.local/bin/` for Phase C shell/Node call-sites to invoke. Until then, in-monorepo callers (diem/council/loom) import `venice_usage` directly and need no PATH entry.

---

## Phase B — Reconciler (`diem venice-usage`)

Depends on Phase A (imports `venice_usage.query_rollup`). Adds the admin key + Venice per-key usage cross-check where the admin key belongs.

> **From the Phase A review — carry into B:** (1) the ledger's `ts` is **naive UTC, second precision** (no offset), chosen for lexicographic `since`/`until` comparison — keep diem's `since`/`until` strings in that exact format or `ts >= ?` silently mis-filters. (2) Phase C call-sites discard the CLI's stderr, so a systemic logging breakage is invisible until reconcile — B's reconcile should explicitly flag projects where **ledger total is 0 but Venice usage > 0** (uncovered/broken), not just numeric deltas.

### Task B1: Wire `VENICE_ADMIN_KEY` into diem config

**Files:**
- Modify: `diem/config.py`
- Test: `tests/diem/test_config.py` (add)

**Interfaces:**
- Produces: `load_venice_admin_key(env_path=Path.home()/".env") -> str` (reads `VENICE_ADMIN_KEY`; `SystemExit(2)` if absent). Refactor the existing dotenv parse in `load_venice_key` into `_read_env(env_path) -> dict[str,str]` and have both use it (DRY).

- [ ] **Step 1: Write the failing test**

```python
# tests/diem/test_config.py  (add)
import pytest
from diem.config import load_venice_admin_key

def test_load_admin_key_reads_venice_admin_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text('VENICE_ADMIN_KEY="sk-admin-xyz"\nVENICE_API_KEY=sk-inf\n')
    assert load_venice_admin_key(env) == "sk-admin-xyz"

def test_load_admin_key_missing_exits_2(tmp_path):
    env = tmp_path / ".env"; env.write_text("VENICE_API_KEY=sk-inf\n")
    with pytest.raises(SystemExit):
        load_venice_admin_key(env)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/diem/test_config.py -v -k admin`
Expected: FAIL — `ImportError: cannot import name 'load_venice_admin_key'`.

- [ ] **Step 3: Write minimal implementation** (refactor `diem/config.py`)

```python
def _read_env(env_path: Path) -> dict:
    try:
        lines = Path(env_path).read_text().splitlines()
    except OSError:
        return {}
    found = {}
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line or line.startswith("#"):
            continue
        name, _, val = line.partition("=")
        found[name.strip()] = val.strip().strip("'\"")
    return found

def load_venice_key(env_path: Path = Path.home() / ".env") -> str:
    found = _read_env(env_path)
    for name in ("VENICE_API_KEY", "VENICE_KEY"):
        if found.get(name):
            return found[name]
    print(f"error: neither VENICE_API_KEY nor VENICE_KEY found in {env_path}",
          file=sys.stderr)
    raise SystemExit(2)

def load_venice_admin_key(env_path: Path = Path.home() / ".env") -> str:
    found = _read_env(env_path)
    if found.get("VENICE_ADMIN_KEY"):
        return found["VENICE_ADMIN_KEY"]
    print(f"error: VENICE_ADMIN_KEY not found in {env_path}", file=sys.stderr)
    raise SystemExit(2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/diem/test_config.py -v`
Expected: PASS (existing `load_venice_key` tests still green — same behavior via `_read_env`).

- [ ] **Step 5: Commit**

```bash
git add diem/config.py tests/diem/test_config.py
git commit -m "feat(diem): load_venice_admin_key + shared _read_env"
```

### Task B2: `UsageClient` — Venice per-key trailing-7d usage

**Files:**
- Create: `diem/usage.py`
- Test: `tests/diem/test_usage.py`

**Interfaces:**
- Produces: `UsageClient(admin_key: str, *, get=None, timeout: int = 30)`; `.per_key_usage() -> list[dict]` where each dict has `key_id: str`, `key_name: str`, `usd: float`, `diem: float` (trailing-7d). `UsageUnavailable(RuntimeError)`.
- Mirrors `BalanceClient` exactly: injectable `get`, `Authorization: Bearer`, dedicated exception, defensive envelope parsing.
- **Endpoint shape — CONFIRMED LIVE 2026-07-19** (read-only probe with the admin key; structure only, no values recorded). `GET /api/v1/api_keys` returns:
  `{"object": str, "data": [ … ]}` — each item has `id: str`, **`description: str`** (the key's name — this is the field the `proj-<project>` convention maps on), `last6Chars`, `apiKeyType`, `limitPeriod`, `lastUsedAt`, `expiresAt`, `consumptionLimits: {usd, vcu, diem}` (the per-key caps), `currentPeriodUsage: {usd: str, diem: str}`, and `usage: {trailingSevenDays: {usd: str, vcu: str, diem: str}}`.
  **The amounts are STRINGS** — the `float(...)` coercion below is required, not merely defensive. Keep the `data`-or-top-level envelope tolerance anyway (mirrors `BalanceClient`). `currentPeriodUsage` is available as an alternative window if the 7-day one proves awkward; `consumptionLimits` exposes the caps if we later want to surface headroom.

- [ ] **Step 1: Write the failing test** (uses the documented shape via a fake `get`)

```python
# tests/diem/test_usage.py
import pytest
from diem.usage import UsageClient, UsageUnavailable

class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status; self._body = body or {}
    def json(self):
        return self._body

def _client(resp=None, exc=None):
    def get(url, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        assert "api_keys" in url
        if exc: raise exc
        return resp
    return UsageClient("sk-admin", get=get)

def test_parses_per_key_trailing_usage():
    body = {"data": [
        {"id": "k1", "description": "proj-romance",
         "usage": {"trailingSevenDays": {"usd": "1.23", "diem": "4.5"}}},
        {"id": "k2", "description": "proj-swimtrack",
         "usage": {"trailingSevenDays": {"usd": "0.10", "diem": "0.4"}}},
    ]}
    keys = _client(FakeResp(body=body)).per_key_usage()
    by = {k["key_name"]: k for k in keys}
    assert by["proj-romance"]["usd"] == 1.23 and by["proj-romance"]["key_id"] == "k1"
    assert by["proj-swimtrack"]["diem"] == 0.4

def test_http_error_raises_unavailable():
    with pytest.raises(UsageUnavailable):
        _client(FakeResp(status=500)).per_key_usage()

def test_network_error_raises_unavailable():
    with pytest.raises(UsageUnavailable):
        _client(exc=ConnectionError("down")).per_key_usage()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/diem/test_usage.py -v`
Expected: FAIL — `No module named 'diem.usage'`.

- [ ] **Step 3: Write minimal implementation**

```python
# diem/usage.py
"""Venice per-key trailing-7-day usage (admin key). Mirrors balance.py:
injectable get, Bearer header, dedicated *Unavailable. Read-only cross-check
for the ledger — never gates anything, so failure just degrades the report."""
from __future__ import annotations
import requests

API_KEYS_URL = "https://api.venice.ai/api/v1/api_keys"

class UsageUnavailable(RuntimeError):
    pass

class UsageClient:
    def __init__(self, admin_key: str, *, get=None, timeout: int = 30):
        self.admin_key = admin_key
        self.timeout = timeout
        self._get = get or requests.get

    def per_key_usage(self) -> list[dict]:
        try:
            r = self._get(API_KEYS_URL,
                          headers={"Authorization": f"Bearer {self.admin_key}"},
                          timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            raise UsageUnavailable(f"api_keys unreachable: {e}") from e
        if getattr(r, "status_code", 200) != 200:
            raise UsageUnavailable(f"api_keys HTTP {r.status_code}")
        try:
            body = r.json()
        except Exception as e:  # noqa: BLE001
            raise UsageUnavailable(f"api_keys non-JSON: {e}") from e
        return self._parse_keys(body)

    @staticmethod
    def _parse_keys(body) -> list[dict]:
        items = body.get("data") if isinstance(body, dict) else body
        if not isinstance(items, list):
            raise UsageUnavailable(f"unexpected api_keys envelope: {body!r:.200}")
        out = []
        for it in items:
            try:
                seven = (it.get("usage", {}) or {}).get("trailingSevenDays", {}) or {}
                out.append({
                    "key_id": str(it.get("id", "")),
                    "key_name": str(it.get("description") or it.get("name") or ""),
                    "usd": float(seven.get("usd") or 0.0),
                    "diem": float(seven.get("diem") or 0.0),
                })
            except Exception as e:  # noqa: BLE001 — ANY per-item parse failure must
                # surface as UsageUnavailable. Note a non-dict item in `data` raises
                # AttributeError from it.get(...), which a narrow (TypeError, ValueError)
                # would let escape unwrapped, violating the contract above.
                raise UsageUnavailable(f"unparseable key row: {e}") from e
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/diem/test_usage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add diem/usage.py tests/diem/test_usage.py
git commit -m "feat(diem): Venice per-key trailing-7d UsageClient"
```

### Task B3: `diem venice-usage` subcommand (reconcile)

**Files:**
- Modify: `diem/cli.py`
- Test: `tests/diem/test_cli.py` (add)

**Interfaces:**
- Produces: `diem venice-usage [--days 7] [--json]` → prints a per-project table: `ledger_usd` (from `venice_usage.query_rollup` over the last N days) vs `venice_usd` (from `UsageClient.per_key_usage`, key→project by the `proj-` prefix) vs `delta`. Rows for a project with no matching key, or a key with no ledger rows, are shown with a `note` (e.g. `no key` / `uncovered`). Degrades gracefully on `UsageUnavailable` (prints ledger-only + a warning, exits 0).
- Consumes: `load_venice_admin_key` (B1), `UsageClient` (B2), `venice_usage.query_rollup` (A3), `_now`/`DiemConfig` (existing).

- [ ] **Step 1: Write the failing test**

```python
# tests/diem/test_cli.py  (add; reuse _cfg_file)
import diem.cli as cli
from datetime import datetime

def test_venice_usage_reconciles_ledger_vs_venice(tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    db = tmp_path / "ledger.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    from venice_usage.ledger import append
    append(project="romance", task_type="draft", model="m", usd=1.00,
           ts="2026-07-18T02:00:00", db_path=db)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 18, 12, 0))
    monkeypatch.setattr(cli, "load_venice_admin_key", lambda: "sk-admin")
    class FakeUsage:
        def __init__(self, *a, **k): pass
        def per_key_usage(self):
            return [{"key_id": "k1", "key_name": "proj-romance", "usd": 1.10, "diem": 4.0}]
    monkeypatch.setattr(cli, "UsageClient", FakeUsage)
    assert cli.main(["venice-usage", "--config", str(cfgp)]) == 0
    out = capsys.readouterr().out
    assert "romance" in out and "1.00" in out and "1.10" in out  # ledger vs venice

def test_venice_usage_degrades_when_venice_unavailable(tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setattr(cli, "load_venice_admin_key", lambda: "sk-admin")
    from diem.usage import UsageUnavailable
    class Down:
        def __init__(self, *a, **k): pass
        def per_key_usage(self): raise UsageUnavailable("down")
    monkeypatch.setattr(cli, "UsageClient", Down)
    assert cli.main(["venice-usage", "--config", str(cfgp)]) == 0   # still exits 0
    assert "unavailable" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/diem/test_cli.py -v -k venice_usage`
Expected: FAIL — `venice-usage` is an unknown diem subcommand (argparse error / no dispatch).

- [ ] **Step 3: Write minimal implementation** (add to `diem/cli.py`)

Add imports at top:
```python
from .usage import UsageClient, UsageUnavailable
from .config import DiemConfig, load_venice_key, load_venice_admin_key
import venice_usage
```
Add the handler:
```python
def _cmd_venice_usage(cfg, now, *, days=7, as_json=False) -> int:
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    ledger = {r["project"]: r["usd"]
              for r in venice_usage.query_rollup(since=since, group_by=("project",))}
    venice = {}
    warn = None
    try:
        for k in UsageClient(load_venice_admin_key()).per_key_usage():
            name = k["key_name"]
            proj = name[len("proj-"):] if name.startswith("proj-") else name
            venice[proj] = venice.get(proj, 0.0) + k["usd"]
    except (UsageUnavailable, SystemExit) as e:
        warn = str(e) or "venice usage unavailable"
    projects = sorted(set(ledger) | set(venice))
    rows = []
    for p in projects:
        lu, vu = ledger.get(p), venice.get(p)
        note = "" if (lu is not None and vu is not None) else \
               ("uncovered" if lu is None else "no key")
        rows.append({"project": p, "ledger_usd": round(lu or 0.0, 4),
                     "venice_usd": None if vu is None else round(vu, 4),
                     "delta": None if (lu is None or vu is None) else round((vu - lu), 4),
                     "note": note})
    if as_json:
        print(json.dumps({"days": days, "warning": warn, "rows": rows}, indent=1))
        return 0
    if warn:
        print(f"warning: Venice usage unavailable ({warn}) — showing ledger only")
    print(f"venice-usage reconcile (last {days}d)")
    print(f"{'project':16} {'ledger$':>9} {'venice$':>9} {'delta$':>9}  note")
    for r in rows:
        vu = "-" if r["venice_usd"] is None else f"{r['venice_usd']:.4f}"
        dl = "-" if r["delta"] is None else f"{r['delta']:+.4f}"
        print(f"{r['project']:16} {r['ledger_usd']:9.4f} {vu:>9} {dl:>9}  {r['note']}")
    return 0
```
Register the subparser (near the others):
```python
vu = sub.add_parser("venice-usage"); vu.add_argument("--config", default=None)
vu.add_argument("--days", type=int, default=7); vu.add_argument("--json", action="store_true")
```
Dispatch (add before `return 1`):
```python
if args.cmd == "venice-usage":
    return _cmd_venice_usage(cfg, now, days=args.days, as_json=args.json)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/diem/ -v`
Expected: PASS (new reconcile tests + existing diem suite green).

- [ ] **Step 5: Commit**

```bash
git add diem/cli.py tests/diem/test_cli.py
git commit -m "feat(diem): venice-usage reconcile subcommand"
```

---

## Phase C — Instrumentation rollout

Wire each call-site to append exactly one row per call. **The ledger append is already tested (Phase A); Phase C is wiring + a per-site verification**, not new logic. Do sites in the order below (highest coverage first). Each site is its own commit.

> **Deploy prerequisite (from the Phase A review):** provision the default ledger dir `~/.local/state/venice-usage/` with correct ownership/permissions at deploy time — call-sites discard stderr, so a bad-perms DB dir would silently drop every row until Phase B reconcile catches it. Also do the post-merge `pipx` install (Task A5 Step 2) so `venice-usage` is on PATH before instrumenting shell/Node sites.

### The instrumentation recipe (per language)

**In-monorepo Python (council, loom, diem-as-caller):** import directly, swallow failures.
```python
try:
    import venice_usage
    venice_usage.append(project="council", task_type=task_type, model=model,
                        tokens_in=usage_in, tokens_out=usage_out, source="council/venice")
except Exception:
    pass  # logging must never break the call
```

**External Python (romance, swimtrack editorial, swimtrack-website image-engine):** shell out (they can't import the pipx-isolated package). Add this ~10-line helper once per project (e.g. `src/venice/usage_log.py`) and call it:
```python
import shutil, subprocess
def usage_log(**kw):
    exe = shutil.which("venice-usage")
    if not exe:
        return
    args = [exe, "log"]
    for k, v in kw.items():
        if v is not None:
            args += [f"--{k.replace('_','-')}", str(v)]
    try:
        subprocess.run(args, timeout=5, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
# usage_log(project="romance", task_type=stage, model=model,
#           tokens_in=tin, tokens_out=tout, source="venice_runtime")
```

**Node (swimtrack-coach, swimtrack-website i18n):** `spawn` detached, ignore result.
```js
import { spawn } from "node:child_process";
export function usageLog(f) {
  try {
    const a = ["log"];
    for (const [k, v] of Object.entries(f))
      if (v != null) a.push(`--${k.replace(/_/g, "-")}`, String(v));
    const c = spawn("venice-usage", a, { stdio: "ignore", detached: true });
    c.on("error", () => {}); c.unref();
  } catch { /* logging must never break the call */ }
}
// usageLog({ project: "swimtrack-coach", task_type: "parse", model: PARSER_MODEL,
//           tokens_in: u.prompt_tokens, tokens_out: u.completion_tokens, source: "parser/venice" });
```

**Shell/curl (romance drafters + generate-covers.sh):** after the curl, extract usage with `python3` (no `jq` dependency) and call the CLI. The response body is already captured for cost printing.
```bash
# $RESP holds the chat/completions JSON; $MODEL the model; $STAGE e.g. draft/edit/qc/scene
read TIN TOUT < <(printf '%s' "$RESP" | python3 -c \
  'import sys,json; u=json.load(sys.stdin).get("usage",{}); print(u.get("prompt_tokens",0), u.get("completion_tokens",0))' 2>/dev/null || echo "0 0")
command -v venice-usage >/dev/null && venice-usage log --project romance --task-type "$STAGE" \
  --model "$MODEL" --tokens-in "$TIN" --tokens-out "$TOUT" --source "$(basename "$0")" 2>/dev/null || true
```
For **image** sites (no token usage): pass `--usd` computed from the model's per-image price already known to the script, `--task-type image` (or `edit-image`), tokens omitted.

### Per-site verification (every site)

After wiring a site, drive it once (or the nearest cheap invocation) against an isolated DB and confirm a row lands:
```bash
export VENICE_USAGE_DB=/tmp/vu-verify.db
# … run the instrumented path once …
venice-usage report --json    # expect the new row with the right project/task_type/model
rm -f /tmp/vu-verify.db*
```
For paths that are expensive/awkward to run live, assert via the project's own test harness where one exists (swimtrack-coach eval harness, council tests) or a targeted unit test that stubs the HTTP layer and asserts `usage_log`/`append` was invoked with the expected fields.

### Site table (order = do top-down)

| # | Site | Lang | project | task_type | source | Notes |
|---|---|---|---|---|---|---|
| 1 | `council/venice.py` `complete()` | py (import) | `council` | `review`/`ask` (pass through from cli) | `council/venice` | **covers all 17 review shims**; thread task_type from `council/cli.py` / `review.py` |
| 2 | `loom/venice.py` `complete()` | py (import) | `loom` | `weave` | `loom/venice` | thread any job-type from `loom/gate.py` if cheap |
| 3 | `romance-empire/src/venice/venice_runtime.py` `_record_usage()` | py (shim) | `romance` | `stage` arg | `venice_runtime` | already has stage+tokens; add `usage_log(...)` beside the CSV write — **keep the CSV** |
| 4 | `romance-empire/src/venice/venice-draft.sh` | sh | `romance` | `draft` | script basename | curl recipe |
| 5 | `…/venice-draft-opus.sh` | sh | `romance` | `draft` | basename | curl recipe |
| 6 | `…/venice-edit.sh` | sh | `romance` | `edit` | basename | curl recipe |
| 7 | `…/venice-edit-opus-editor.sh` | sh | `romance` | `edit` | basename | curl recipe |
| 8 | `…/venice-edit-sonnet-editor.sh` | sh | `romance` | `edit` | basename | curl recipe |
| 9 | `…/venice-qc.sh` | sh | `romance` | `qc` | basename | curl recipe |
| 10 | `…/venice-scene.sh` | sh | `romance` | `scene` | basename | curl recipe |
| 11 | `…/new-book.sh` | sh | `romance` | `scaffold` | basename | curl recipe (chat) |
| 12 | `romance-empire/src/social/generate_images.py` | py (shim) | `romance` | `image` | `generate_images` | image: pass `--usd` from its cost calc |
| 13 | `romance-empire/src/social/condition_images.py` | py (shim) | `romance` | `edit-image` | `condition_images` | image edit |
| 14 | `romance-empire/generated-images/generate_all.py` | py (shim) | `romance` | `image` | `generate_all` | image; (partial-key print fix tracked separately) |
| 15 | `romance-empire/src/venice/generate-covers.sh` | sh | `romance` | `image` | basename | image curl |
| 16 | `swimtrack/editorial/src/editorial/venice.py` `complete()` | py (shim) | `swimtrack` | `editorial` | `editorial/venice` | tokens from response `usage` |
| 17 | `swimtrack-coach/lib/parser/venice.ts` `parseWorkout()` | node | `swimtrack-coach` | `parse` | `parser/venice` | openai SDK resp `.usage` |
| 18 | `swimtrack-website/tools/i18n/venice.mjs` `completeJSON()` | node | `swimtrack-website` | `translate` | `i18n/venice` | fetch resp `usage` |
| 19 | `swimtrack-website/tools/image-engine/engine/venice.py` `generate()` | py (shim) | `swimtrack-website` | `image` (or `asset_type`) | `image-engine` | image; `--usd` from its pricing |
| 20 | `.claude/skills/venice-ai/scripts/generate.py` | py (shim) | `venice-ai-skill` | `image`/`video` | `venice-ai-skill` | image + video subcommands |
| 21 | `romance-empire/src/social/reels.py` | py (shim) | `romance` | `video` | `reels` | ⚠️ **found during execution — missing from the original inventory.** Async video (`/video/queue` + poll loop) |

> **Async video rule (sites 20 & 21):** log exactly **one row per generation, at submit** — never inside the poll loop, and not on retrieval. Submit fires once regardless of poll count and still counts jobs Venice bills but the client never successfully retrieves (timeout/crash/network drop); retrieval-based logging silently undercounts those.

> **`venice-edit.sh` / `venice-qc.sh` nuance:** these `exec` into `edit_pipeline.py` by default and only use curl under `VENICE_EDIT_LEGACY=1` / `VENICE_QC_LEGACY=1`. Production edit/qc traffic therefore flows through the centrally-instrumented `venice_runtime.VeniceClient.complete()`. The paths are mutually exclusive, so instrumenting both cannot double-count.

> 🔒 **Standing rule — test isolation (learned the hard way).** An instrumented client calls `append(db_path=None)`, which resolves `$VENICE_USAGE_DB` at call time and **falls back to the REAL ledger when unset**. Any test that drives an instrumented function without setting it writes junk into production billing data — this actually happened in 4 of 5 repos (53 junk rows, surgically removed). **Every instrumented repo must have a suite-wide guard**, not per-test discipline: pytest → an autouse `conftest.py` fixture setting `VENICE_USAGE_DB` to a tmp path; vitest → a `setupFiles` entry doing `process.env.VENICE_USAGE_DB ||= <tmp>`; stdlib unittest → `os.environ.setdefault(...)` in the tests package `__init__.py`. Verify by measuring the real ledger's row count before and after a full suite run — it must be unchanged.

> The live deployed copies under `/home/dev/loom-runtime/` (council/loom/diem) are refreshed from source on the normal deploy path — instrument source, then redeploy/reinstall, don't hand-edit the runtime tree. Do **not** edit `build/lib/**`, `.next/**`, `**/.claude/worktrees/**`, or `dump/**` copies.

---

## Phase D — Ops (keys, old-key retirement, Telegram) — no TDD gate

Sequenced after the ledger exists so wiring + reconcile can be validated end-to-end. **The user mints + caps keys on the Venice site and pastes values via a hidden prompt — never print a value, never put one on a command line or in shell history.**

> **Live evidence gathered 2026-07-19/20 that shapes Phase D:**
> 1. **romance-empire currently spends on the `DEFAULT` key, not its own.** A real production run (20 videos + 24 images, **$27.45**) logged to the ledger while Phase C was landing; Venice shows `DEFAULT` last-used at that moment, while the key actually *named* `romance-empire` hadn't been touched for two days. This is exactly the mis-attribution per-project keys exist to fix — D1 is not cosmetic.
> 2. **Venice usage figures lag.** `DEFAULT` reported `trailingSevenDays.usd = 0.0000` *and* `currentPeriodUsage.usd = 0.0000` minutes after real spend on it. So `diem venice-usage` is a **drift detector over days, not a real-time cross-check** — a fresh same-day delta is expected and is not a bug. Don't chase it; compare over a multi-day window.
> 3. **The reconcile splits a project in two until names are harmonized** — the live table showed `romance` (ledger $27.45, note `no key`) and `romance-empire` (Venice $7.55, note `uncovered`) as separate rows. Renaming the key to the canonical tag (D5) collapses them into one reconciled line.
> 4. **Consider an `off_box` list in diem's config** so keys for other machines (`MacWhisper`, `OpenClaw`, `Claude Code`, `n8n`, `GameBuilding`) render as `off-box` rather than `uncovered`. Today the `uncovered` column conflates "instrumentation is broken" (actionable) with "runs on another machine" (expected forever), which blunts the signal.

### D1: Mint + wire per-project keys (each independently)

For each project below: (a) user mints a capped inference key named `proj-<project>` on Venice; (b) create a project-local `.env` (chmod 600) holding the key under the **var name the code already reads**; (c) add a `source ./.env` (or equivalent) shim to each entry point so the ambient env carries it. Verify the app still runs and a ledger row appears tagged to that project.

| Project | Key var name (verified in code) | Entry points to add the source shim |
|---|---|---|
| romance-empire | `VENICE_KEY` | `src/venice/*.sh` headers + the python entry (`edit_pipeline.py`, `visual_strategy.py` read `os.environ`) |
| swimtrack (editorial) | `VENICE_INFERENCE_KEY` | `editorial` CLI entry (`config.py:get_api_key`) |
| swimtrack-coach | `VENICE_API_KEY` (+ `VENICE_BASE_URL`) | Next.js runtime env / `.env.local` |
| swimtrack-website (i18n) | `VENICE_API_KEY` | `tools/i18n/translate.mjs` runner |
| swimtrack-website (image-engine) | `VENICE_INFERENCE_KEY` | `tools/image-engine/.env` (already parsed) |
| council | `VENICE_API_KEY` | ambient (`~/.env`) — mint a dedicated `proj-council` or keep DEFAULT; user decides |
| loom | `VENICE_API_KEY` | ambient (`~/.env`) — same decision as council |

### D2: Retire the old shared Venice key

- Migrate the remaining old-shared-key copies onto scoped keys: `~/.claude/skills/venice-ai/.env`, `swimtrack-coach/.env.local`, `swimtrack-website/.env`. (The `~/.hermes/.env` copy is removed by the parallel Hermes retirement.)
- Verify each consumer works on its scoped key.
- **Then** the user revokes the old shared key on Venice. Confirm nothing breaks (council review CI already uses its own CI key; DEFAULT/ADMIN in `~/.env` are separate).

### D3: Consolidate the Telegram bot token

- After Hermes retirement, **2** copies remain: `~/.config/diem/config.toml` (`[telegram] bot_token`) and `~/.claude/channels/telegram/.env` (`TELEGRAM_BOT_TOKEN`). (Hermes held the 3rd as `BOT_TOKEN`.)
- Consolidate to a single source. Recommendation: keep the token in one place (`~/.env` as `TELEGRAM_BOT_TOKEN`, already auto-loaded) and have diem read it via config fallback + the channel `.env` `source` it, rather than two independent literals. Confirm with the user before changing diem's config-loading contract; if they prefer the status quo of two copies, at least document them as intentional. (Low urgency — single-user box.)

### D4: Keep the map current

After each of D1–D3 and each Phase-C batch, update `/home/dev/docs/SECRETS-AND-DEPLOY-MAP.md` + `secrets-map-notes-2026-07-18.md`: flip the Phase-2b line items, record the new per-project keys (names only), and mark the old shared key retired once revoked.

---

## Sequencing & dependencies

1. **Phase A** (ledger core) — no dependencies; must land first (everything needs `venice-usage` on PATH).
2. **Phase B** (reconciler) — needs A. Can proceed as soon as A is installed.
3. **Phase C** (instrumentation) — needs A installed; sites are independent of each other, do highest-coverage first (council → romance runtime → the rest). Reconcile (B) becomes meaningful only once C has populated the ledger.
4. **Phase D** (ops) — D1/D2 can start any time (independent of A–C) but are best validated *after* A+C so a per-project ledger row confirms the wiring. D3 needs Hermes retirement done (in flight). D2's final revoke is last.
5. **Hermes retirement** — running in parallel now (separate delegated worker); it removes one Telegram copy + one old-shared-key copy, simplifying D2/D3.

---

## Self-Review

**Spec coverage vs the 2b brief:**
- Item 1 (mint + wire per-project keys) → **Phase D1** (+ var-name table verified against code).
- Item 2 (retire old shared key) → **Phase D2**.
- Item 3 (SQLite ledger + instrument ~8 modules) → **Phase A** (ledger) + **Phase C** (all ~12 code + ~9 shell sites; corrected count).
- Item 4 (reporter rolling up project × task_type, reconciled vs Venice) → **Phase A `report`** (offline rollup) + **Phase B `diem venice-usage`** (reconcile).
- Item 5 (consolidate Telegram token ×3) → **Phase D3** (now ×2 post-Hermes).
- Locked decision "passive ledger, no proxy" → append-only, best-effort, no hot-path component. ✓
- Locked decision "romance enriches existing usage-log" → **D2 answer**: central CLI (task_type=stage) **and** the CSV kept. ✓
- Locked decision "decouple billing identity (keys) from analytics (project × task_type)" → keys are per-project (D1); analytics is the ledger's project×task_type (A/B), independent of key granularity. ✓

**Placeholder scan:** no "TBD/implement later/add error handling" left as work — the one genuine unknown (Venice per-key usage response shape, Task B2) is called out with a defensive parser + a confirmation step, not a placeholder.

**Type consistency:** `append()` / `query_rollup()` signatures match their callers in A4 and B3; `UsageClient.per_key_usage()` dict keys (`key_id,key_name,usd,diem`) match B3's consumer; `load_venice_admin_key` name matches B3's import.
