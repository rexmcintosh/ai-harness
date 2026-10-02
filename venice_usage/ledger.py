from __future__ import annotations
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

def production_db() -> Path:
    """THE ledger — the one with the real money in it.

    A function, not a constant, for two reasons: `Path.home()` must be read at
    call time, and a test needs one place to redirect so it can exercise the
    "this is production" branch without going anywhere near the real file."""
    return Path.home() / ".local/state/venice-usage/ledger.db"


def default_db() -> Path:
    # Resolve at CALL time (not import) so $VENICE_USAGE_DB set later — e.g. by a
    # test's monkeypatch.setenv — is honored.
    env = os.environ.get("VENICE_USAGE_DB")
    if env:  # absent OR empty-string -> use the default path
        return Path(env)
    return production_db()


def is_production_db(db_path=None) -> bool:
    """True when this path is the production ledger.

    Resolved on both sides, so a symlink or a `~` cannot smuggle a write past
    the check. Anything unresolvable is treated as NOT production — the caller
    of this predicate refuses on True, and refusing to log an honest call is a
    worse failure than the one it would be guarding against."""
    target = Path(db_path) if db_path else default_db()
    try:
        return target.expanduser().resolve() == production_db().resolve()
    except OSError:
        return False

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
  source     TEXT,
  ext_id     TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS ix_usage_proj_task ON usage(project, task_type);
"""

# ext_id is the idempotency key for rows that arrive from somewhere else — today
# that is the CI merge gate, whose runner-local ledger is destroyed with the job
# (see venice_usage/portable.py). It is NULL for every locally-appended row, so
# the uniqueness constraint has to be partial: two identical local rows are a
# legitimate two calls, but the same CI row ingested twice is one call.
_EXT_INDEX = ('CREATE UNIQUE INDEX IF NOT EXISTS ux_usage_ext_id '
              'ON usage(ext_id) WHERE ext_id IS NOT NULL')


def _migrate(conn) -> None:
    """Add ext_id to a ledger written before this column existed. Cheap enough
    to run on every connect (one PRAGMA), and the only safe place to put it —
    callers open a fresh connection per append."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(usage)")}
    if cols and "ext_id" not in cols:
        conn.execute("ALTER TABLE usage ADD COLUMN ext_id TEXT")

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()

def connect(db_path=None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else default_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.execute(_EXT_INDEX)
    conn.commit()
    return conn

def append(*, project, task_type, model, tokens_in=0, tokens_out=0,
           usd=None, source=None, ts=None, ext_id=None, db_path=None) -> int:
    """Append one usage row; returns its rowid, or 0 when an ext_id row was
    already present (INSERT OR IGNORE). ext_id makes re-ingesting the same CI
    artifact a no-op instead of double-counting the spend."""
    ts = ts or _utcnow_iso()
    if usd is None:
        from .pricing import estimate_usd
        usd = estimate_usd(model, int(tokens_in), int(tokens_out))
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO"
            " usage(ts,project,task_type,model,tokens_in,tokens_out,usd,source,ext_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, project, task_type, model, int(tokens_in), int(tokens_out), usd,
             source, ext_id))
        conn.commit()
        return cur.lastrowid if cur.rowcount else 0

_GROUP_COLS = {"project", "task_type", "model", "source"}

def query_rollup(*, since=None, until=None, project=None,
                 group_by=("project", "task_type"), db_path=None,
                 price_basis="current") -> list[dict]:
    """Roll the ledger up, valued on one of two explicit bases.

    `price_basis="current"` (the default) values every row from the CURRENT
    price table on read; `"stored"` sums the `usd` column exactly as it was
    written. Neither ever writes: stored rows are an audit trail of what each
    call was estimated at when it was logged, and rewriting them would both
    destroy that and clobber the real per-asset figures the image engine passes
    with `--usd`. See `pricing.basis_line` for the header every report prints —
    a rollup whose basis is not stated is how a 2.5x error reached an audit.

    Each row also carries `unpriced_calls`: calls the chosen basis could put no
    number on at all, so a zero is never mistaken for free."""
    from .pricing import BASES, PRICES
    if price_basis not in BASES:
        raise ValueError(f"unknown price_basis {price_basis!r}; expected one of {BASES}")
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
    # Group by the caller's columns PLUS model, always. Price is per model and
    # linear in tokens, so repricing a (cols, model) subgroup from its token
    # sums is exact, and folding the subgroups back up afterwards costs nothing.
    inner = tuple(dict.fromkeys(group_by + ("model",)))
    cols = ", ".join(inner)
    sql = (f"SELECT {cols}, COUNT(*) AS calls, "
           "COALESCE(SUM(tokens_in),0) AS tokens_in, "
           "COALESCE(SUM(tokens_out),0) AS tokens_out, "
           "COALESCE(SUM(usd),0.0) AS usd, "
           "SUM(CASE WHEN usd IS NULL THEN 1 ELSE 0 END) AS null_usd "
           f"FROM usage{clause} GROUP BY {cols}")
    with closing(connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        subgroups = [dict(r) for r in conn.execute(sql, params).fetchall()]

    out: dict[tuple, dict] = {}
    for sub in subgroups:
        rate = PRICES.get(sub["model"])
        if price_basis == "current" and rate is not None:
            usd = sub["tokens_in"] / 1e6 * rate["input"] + \
                  sub["tokens_out"] / 1e6 * rate["output"]
            unpriced = 0
        else:
            # No table row (an image model's real --usd, a retired id, a test
            # double), or the caller asked for the stored basis: take what is
            # written and count what was written as nothing.
            usd, unpriced = sub["usd"], sub["null_usd"]
        key = tuple(sub[c] for c in group_by)
        row = out.setdefault(key, {**{c: sub[c] for c in group_by}, "calls": 0,
                                   "tokens_in": 0, "tokens_out": 0, "usd": 0.0,
                                   "unpriced_calls": 0})
        row["calls"] += sub["calls"]
        row["tokens_in"] += sub["tokens_in"]
        row["tokens_out"] += sub["tokens_out"]
        row["usd"] += usd
        row["unpriced_calls"] += unpriced
    rows = sorted(out.values(), key=lambda r: -r["usd"])
    # Float addition over REAL accumulates binary error (e.g. 0.09+0.01 ->
    # 0.09999999999999999); round to the same 6-decimal precision pricing.py's
    # estimate_usd() uses, so aggregated usd matches cent-level expectations.
    for r in rows:
        r["usd"] = round(r["usd"], 6)
    return rows
