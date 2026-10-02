# DIEM Drain Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `diem` CLI + 3 cron checkpoints that drain the day's unspent Venice DIEM into queued/discovered workloads before the 01:00 reset, deterministically and behind existing human gates.

**Architecture:** New package `diem/` in this repo (second console script in the existing `council` distribution). diem owns only queue/clock/budget; runners shell out to `council`, `loom.cli backfill`, and repo-declared standing-order commands. State in `~/.local/state/diem/`, config in `~/.config/diem/config.toml`.

**Tech Stack:** Python ≥3.11 (stdlib `tomllib`, `dataclasses`, `subprocess`, `uuid`), `requests` (already a dep), pytest with injectable fakes (repo convention: see `tests/conftest.py` FakeClient).

**Spec:** `docs/superpowers/specs/2026-07-03-diem-drain-engine-design.md` — read it first.

## Global Constraints

- Python ≥3.11; **no new runtime dependencies** (only `requests>=2.31`, already declared).
- All time math in **local time**; reset `01:00`, hard deadline `00:50`, both from config.
- **Never drain blind:** balance endpoint failure aborts the checkpoint.
- **Read-and-stage only:** no runner may commit, push, merge, or publish. `cmd` type only runs whitelisted argv.
- Every module takes its collaborators (clock, HTTP post/get, subprocess runner, paths) as injectable parameters — repo test convention.
- Item ids: `uuid.uuid4().hex` (spec said "ulid"; uuid4 keeps zero deps — `created` field carries ordering).
- Floors are fractions of `daily_diem` (config value = your daily DIEM allowance), compared against the live balance.
- Commit after every green test cycle. Conventional-commit style, e.g. `feat(diem): ...`.

## File Structure

```
diem/__init__.py       (empty marker)
diem/config.py         DiemConfig.load(), load_venice_key()      — Task 1
diem/queue.py          Item, new_item(), QueueDir                — Task 2
diem/balance.py        BalanceClient, BalanceUnavailable         — Task 3
diem/state.py          Estimates, Reviewed, Lock, pause helpers  — Task 4
diem/discover.py       discover() → hygiene + feedstock items    — Task 5
diem/runners.py        run_item() → RunResult                    — Task 6
diem/drain.py          run_checkpoint() drain loop               — Task 7
diem/report.py         morning report, ping, send_telegram()     — Task 8
diem/cli.py            argparse entry `diem`                     — Task 9
diem/README.md         ops doc + crontab lines                   — Task 10
tests/diem/            test_<module>.py per module, conftest.py
pyproject.toml         packages += diem, scripts += diem         — Task 1
```

---

### Task 1: Package scaffold, config loading, key loading

**Files:**
- Create: `diem/__init__.py`, `diem/config.py`, `tests/diem/__init__.py`, `tests/diem/test_config.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `DiemConfig` dataclass with fields `repos: list[Path]`, `checkpoints: list[Checkpoint]` (`Checkpoint(time: str, floor: float)`), `deadline: str`, `reset: str`, `daily_diem: float`, `state_dir: Path`, `outputs_dir: Path`, `loom_repo: Path`, `loom_cmd: list[str]`, `seeds: dict[str, dict]`, `telegram: dict | None`, `cmd_whitelist: dict[str, dict]`, `backfill_max_per_night: int`, `backfill_chunk: int`. Classmethod `DiemConfig.load(path: Path | None = None) -> DiemConfig`.
- Produces: `load_venice_key(env_path: Path = Path.home()/".env") -> str` — raises `SystemExit(2)` with message if neither name found.

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_config.py
import textwrap
from pathlib import Path
import pytest
from diem.config import DiemConfig, load_venice_key

TOML = textwrap.dedent("""
    daily_diem = 100.0
    repos = ["/home/dev/projects/swimtrack"]
    deadline = "00:50"
    reset = "01:00"
    state_dir = "{state}"
    outputs_dir = "{out}"
    loom_repo = "/home/dev/projects/build-ai-automation-workflow"
    loom_cmd = ["/home/dev/projects/build-ai-automation-workflow/.venv/bin/python", "-m", "loom.cli", "backfill"]

    [[checkpoints]]
    time = "21:00"
    floor = 0.40
    [[checkpoints]]
    time = "23:00"
    floor = 0.15
    [[checkpoints]]
    time = "00:15"
    floor = 0.0

    [seeds.images]
    cost = 2.0
    duration_s = 180
    [seeds.ask]
    cost = 0.5
    duration_s = 120

    [telegram]
    bot_token = "tok"
    chat_id = "123"

    [cmd_whitelist.teasers]
    repo = "/home/dev/projects/romance-empire"
    argv = ["python", "scripts/make_teasers.py"]
""")

def _write_cfg(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(TOML.format(state=tmp_path / "state", out=tmp_path / "out"))
    return p

def test_load_parses_all_sections(tmp_path):
    cfg = DiemConfig.load(_write_cfg(tmp_path))
    assert cfg.daily_diem == 100.0
    assert cfg.checkpoints[0].time == "21:00" and cfg.checkpoints[0].floor == 0.40
    assert cfg.checkpoints[2].floor == 0.0
    assert cfg.repos == [Path("/home/dev/projects/swimtrack")]
    assert cfg.seeds["images"]["cost"] == 2.0
    assert cfg.telegram["chat_id"] == "123"
    assert cfg.cmd_whitelist["teasers"]["argv"][0] == "python"
    assert cfg.state_dir == tmp_path / "state"

def test_load_defaults(tmp_path):
    p = tmp_path / "min.toml"
    p.write_text('daily_diem = 50.0\nrepos = []\n')
    cfg = DiemConfig.load(p)
    assert cfg.deadline == "00:50" and cfg.reset == "01:00"
    assert cfg.telegram is None
    assert cfg.backfill_max_per_night == 4 and cfg.backfill_chunk == 2
    assert cfg.state_dir == Path.home() / ".local/state/diem"

def test_load_missing_daily_diem_exits(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("repos = []\n")
    with pytest.raises(SystemExit):
        DiemConfig.load(p)

@pytest.mark.parametrize("line", [
    'VENICE_API_KEY=sk-abc123',
    'VENICE_KEY="sk-abc123"',
    "export VENICE_API_KEY='sk-abc123'",
])
def test_load_venice_key_variants(tmp_path, line):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=x\n{line}\n")
    assert load_venice_key(env) == "sk-abc123"

def test_load_venice_key_missing_exits(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=x\n")
    with pytest.raises(SystemExit):
        load_venice_key(env)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dev/projects/build-ai-automation-workflow && python -m pytest tests/diem/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem'`

- [ ] **Step 3: Implement**

Create empty `diem/__init__.py` and `tests/diem/__init__.py`, then:

```python
# diem/config.py
"""Config + key loading. Cron has no shell env, so the Venice key is read
straight from ~/.env (accepts VENICE_API_KEY or VENICE_KEY)."""
from __future__ import annotations
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "diem" / "config.toml"


@dataclass(frozen=True)
class Checkpoint:
    time: str   # "HH:MM" local
    floor: float  # fraction of daily_diem


_DEFAULT_CHECKPOINTS = [Checkpoint("21:00", 0.40), Checkpoint("23:00", 0.15),
                        Checkpoint("00:15", 0.0)]
_DEFAULT_SEEDS = {
    "ask": {"cost": 0.5, "duration_s": 120},
    "review": {"cost": 1.0, "duration_s": 180},
    "images": {"cost": 2.0, "duration_s": 180},
    "backfill": {"cost": 1.0, "duration_s": 300},
    "cmd": {"cost": 1.0, "duration_s": 300},
}


@dataclass
class DiemConfig:
    daily_diem: float
    repos: list[Path]
    checkpoints: list[Checkpoint] = field(default_factory=lambda: list(_DEFAULT_CHECKPOINTS))
    deadline: str = "00:50"
    reset: str = "01:00"
    state_dir: Path = Path.home() / ".local/state/diem"
    outputs_dir: Path = Path.home() / ".local/state/diem/outputs"
    loom_repo: Path = Path.home() / "projects/build-ai-automation-workflow"
    loom_cmd: list[str] = field(default_factory=lambda: [
        str(Path.home() / "projects/build-ai-automation-workflow/.venv/bin/python"),
        "-m", "loom.cli", "backfill"])
    seeds: dict = field(default_factory=lambda: dict(_DEFAULT_SEEDS))
    telegram: dict | None = None
    cmd_whitelist: dict = field(default_factory=dict)
    backfill_max_per_night: int = 4
    backfill_chunk: int = 2

    @classmethod
    def load(cls, path: Path | None = None) -> "DiemConfig":
        path = path or DEFAULT_CONFIG
        try:
            raw = tomllib.loads(Path(path).read_text())
        except FileNotFoundError:
            print(f"error: no config at {path}", file=sys.stderr)
            raise SystemExit(2)
        if "daily_diem" not in raw:
            print("error: config needs daily_diem (your daily DIEM allowance)",
                  file=sys.stderr)
            raise SystemExit(2)
        kw = {"daily_diem": float(raw["daily_diem"]),
              "repos": [Path(r) for r in raw.get("repos", [])]}
        if "checkpoints" in raw:
            kw["checkpoints"] = [Checkpoint(c["time"], float(c["floor"]))
                                 for c in raw["checkpoints"]]
        for key in ("deadline", "reset", "backfill_max_per_night", "backfill_chunk"):
            if key in raw:
                kw[key] = raw[key]
        for key in ("state_dir", "outputs_dir", "loom_repo"):
            if key in raw:
                kw[key] = Path(raw[key])
        if "loom_cmd" in raw:
            kw["loom_cmd"] = list(raw["loom_cmd"])
        seeds = dict(_DEFAULT_SEEDS)
        seeds.update(raw.get("seeds", {}))
        kw["seeds"] = seeds
        kw["telegram"] = raw.get("telegram")
        kw["cmd_whitelist"] = raw.get("cmd_whitelist", {})
        return cls(**kw)


def load_venice_key(env_path: Path = Path.home() / ".env") -> str:
    try:
        lines = Path(env_path).read_text().splitlines()
    except OSError:
        lines = []
    found = {}
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line or line.startswith("#"):
            continue
        name, _, val = line.partition("=")
        found[name.strip()] = val.strip().strip("'\"")
    for name in ("VENICE_API_KEY", "VENICE_KEY"):
        if found.get(name):
            return found[name]
    print(f"error: neither VENICE_API_KEY nor VENICE_KEY found in {env_path}",
          file=sys.stderr)
    raise SystemExit(2)
```

And in `pyproject.toml` change:

```toml
[project.scripts]
council = "council.cli:main"
diem = "diem.cli:main"

[tool.setuptools]
packages = ["council", "diem"]
```

(`diem.cli` doesn't exist until Task 9 — that's fine; nothing imports it until the script is invoked.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_config.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/ tests/diem/ pyproject.toml
git commit -m "feat(diem): package scaffold, config + Venice key loading"
```

---

### Task 2: Queue — items, dedupe, priority, expiry

**Files:**
- Create: `diem/queue.py`, `tests/diem/test_queue.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Item` dataclass (`id, type, banked, priority, payload, created, expires, attempts, max_attempts`) with `.dedupe_key() -> str`, `.to_json() -> str`, classmethod `Item.from_json(text)`. `new_item(type: str, payload: dict, *, banked=False, expires=None, created: str) -> Item` (caller supplies `created` ISO string — keeps clock injectable). `QueueDir(root: Path)` with `.add(item) -> bool` (False + no write on duplicate `dedupe_key` among pending), `.pending(now_iso: str) -> list[Item]` (expired items auto-archived, result sorted banked-first then type priority `ask=0, images=1, review=2, cmd=2, backfill=3`, then `created`), `.archive(item, outcome: dict)`, `.remove(item_id) -> bool`, `.requeue(item)` (rewrite a pending item file, e.g. after `attempts += 1`), `.night_count(type_, since_iso: str) -> int` (pending + archived items with `created >= since_iso`, for per-night caps), `.archived_keys_since(since_iso: str) -> set[str]` (dedupe keys of items archived with `created >= since_iso` — lets discovery enforce "once per night" past archive time).

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_queue.py
from diem.queue import Item, QueueDir, new_item

NOW = "2026-07-03T21:00:00"

def _q(tmp_path):
    return QueueDir(tmp_path / "state")

def test_roundtrip_json():
    it = new_item("ask", {"question": "q?", "panel": "decision"}, created=NOW)
    back = Item.from_json(it.to_json())
    assert back == it and back.attempts == 0 and back.max_attempts == 2

def test_add_and_pending_priority_order(tmp_path):
    q = _q(tmp_path)
    q.add(new_item("backfill", {"max_targets": 2}, created=NOW))
    q.add(new_item("review", {"repo": "/r/a", "diff": True}, created=NOW))
    q.add(new_item("images", {"repo": "/r/b", "count": 5}, created=NOW))
    banked = new_item("review", {"repo": "/r/c", "diff": True}, banked=True, created=NOW)
    q.add(banked)
    types = [(i.banked, i.type) for i in q.pending(NOW)]
    assert types == [(True, "review"), (False, "images"), (False, "review"),
                     (False, "backfill")]

def test_dedupe_same_key_rejected(tmp_path):
    q = _q(tmp_path)
    assert q.add(new_item("review", {"repo": "/r/a", "diff": True}, created=NOW))
    assert not q.add(new_item("review", {"repo": "/r/a", "diff": True}, created=NOW))
    # different repo is a different key
    assert q.add(new_item("review", {"repo": "/r/b", "diff": True}, created=NOW))

def test_expired_items_are_archived_not_returned(tmp_path):
    q = _q(tmp_path)
    q.add(new_item("ask", {"question": "old", "panel": "decision"},
                   created="2026-07-01T10:00:00", expires="2026-07-02T00:00:00"))
    assert q.pending(NOW) == []
    assert q.night_count("ask", "2026-07-01T00:00:00") == 1  # archived, still counted
    assert q.night_count("ask", "2026-07-03T01:00:00") == 0  # outside window

def test_archived_keys_since_and_requeue(tmp_path):
    q = _q(tmp_path)
    it = new_item("review", {"repo": "/r/a", "diff": True}, created=NOW)
    q.add(it)
    q.archive(it, {"ok": True})
    assert it.dedupe_key() in q.archived_keys_since("2026-07-03T01:00:00")
    assert q.archived_keys_since("2026-07-04T01:00:00") == set()
    it2 = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW)
    q.add(it2)
    it2.attempts = 1
    q.requeue(it2)
    assert q.pending(NOW)[0].attempts == 1

def test_archive_and_remove(tmp_path):
    q = _q(tmp_path)
    it = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW)
    q.add(it)
    q.archive(it, {"ok": True})
    assert q.pending(NOW) == []
    it2 = new_item("ask", {"question": "q2", "panel": "decision"}, created=NOW)
    q.add(it2)
    assert q.remove(it2.id) and q.pending(NOW) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.queue'`

- [ ] **Step 3: Implement**

```python
# diem/queue.py
"""File-per-item queue: writing a JSON file into queue/ IS banking an item,
so any Claude session can enqueue without going through the CLI."""
from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

_TYPE_PRIORITY = {"ask": 0, "images": 1, "review": 2, "cmd": 2, "backfill": 3}


@dataclass
class Item:
    id: str
    type: str
    banked: bool
    priority: int
    payload: dict
    created: str
    expires: str | None = None
    attempts: int = 0
    max_attempts: int = 2

    def dedupe_key(self) -> str:
        p = self.payload
        if self.type == "review":
            return f"review:{p.get('repo')}:{'diff' if p.get('diff') else p.get('range')}"
        if self.type == "images":
            return f"images:{p.get('repo')}"
        if self.type == "ask":
            return f"ask:{p.get('question')}"
        if self.type == "cmd":
            return f"cmd:{p.get('name')}"
        return f"{self.type}:{self.id}"  # backfill chunks never dedupe

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_json(cls, text: str) -> "Item":
        return cls(**json.loads(text))


def new_item(type: str, payload: dict, *, banked: bool = False,
             expires: str | None = None, created: str,
             max_attempts: int = 2) -> Item:
    return Item(id=uuid.uuid4().hex, type=type, banked=banked,
                priority=_TYPE_PRIORITY.get(type, 5), payload=payload,
                created=created, expires=expires, max_attempts=max_attempts)


class QueueDir:
    def __init__(self, root: Path):
        self.qdir = Path(root) / "queue"
        self.adir = Path(root) / "archive"
        self.qdir.mkdir(parents=True, exist_ok=True)
        self.adir.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> list[Item]:
        items = []
        for f in sorted(self.qdir.glob("*.json")):
            try:
                items.append(Item.from_json(f.read_text()))
            except (json.JSONDecodeError, TypeError):
                f.rename(self.adir / f"corrupt-{f.name}")
        return items

    def add(self, item: Item) -> bool:
        keys = {i.dedupe_key() for i in self._load_all()}
        if item.dedupe_key() in keys:
            return False
        (self.qdir / f"{item.id}.json").write_text(item.to_json())
        return True

    def pending(self, now_iso: str) -> list[Item]:
        live = []
        for it in self._load_all():
            if it.expires and it.expires <= now_iso:
                self.archive(it, {"ok": False, "error": "expired"})
            else:
                live.append(it)
        return sorted(live, key=lambda i: (not i.banked, i.priority, i.created))

    def archive(self, item: Item, outcome: dict) -> None:
        rec = json.loads(item.to_json())
        rec["outcome"] = outcome
        (self.adir / f"{item.id}.json").write_text(json.dumps(rec, indent=1))
        (self.qdir / f"{item.id}.json").unlink(missing_ok=True)

    def remove(self, item_id: str) -> bool:
        p = self.qdir / f"{item_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def requeue(self, item: Item) -> None:
        (self.qdir / f"{item.id}.json").write_text(item.to_json())

    def night_count(self, type_: str, since_iso: str) -> int:
        n = 0
        for d in (self.qdir, self.adir):
            for f in d.glob("*.json"):
                try:
                    rec = json.loads(f.read_text())
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == type_ and rec.get("created", "") >= since_iso:
                    n += 1
        return n

    def archived_keys_since(self, since_iso: str) -> set[str]:
        keys = set()
        for f in self.adir.glob("*.json"):
            try:
                rec = json.loads(f.read_text())
                rec.pop("outcome", None)
                item = Item(**rec)
            except (json.JSONDecodeError, TypeError):
                continue
            if item.created >= since_iso:
                keys.add(item.dedupe_key())
        return keys
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_queue.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/queue.py tests/diem/test_queue.py
git commit -m "feat(diem): file-per-item queue with dedupe, priority, expiry"
```

---

### Task 3: Balance client

**Files:**
- Create: `diem/balance.py`, `tests/diem/test_balance.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `BalanceUnavailable(RuntimeError)`; `BalanceClient(api_key, *, get=None, timeout=30)` with `.diem_balance() -> float`. Parses Venice `GET /api/v1/api_keys/rate_limits`; accepts the balance under `data.balances.DIEM` or top-level `balances.DIEM` (defensive — verify the exact envelope against the live API in Task 10 smoke test). Any HTTP/parse failure raises `BalanceUnavailable`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_balance.py
import pytest
from diem.balance import BalanceClient, BalanceUnavailable

class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
    def json(self):
        return self._body

def _client(resp=None, exc=None):
    def get(url, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        if exc:
            raise exc
        return resp
    return BalanceClient("sk-test", get=get)

def test_parses_nested_data_balances():
    c = _client(FakeResp(body={"data": {"balances": {"DIEM": 42.5, "USD": 1.0}}}))
    assert c.diem_balance() == 42.5

def test_parses_top_level_balances():
    c = _client(FakeResp(body={"balances": {"DIEM": 7}}))
    assert c.diem_balance() == 7.0

def test_http_error_raises_unavailable():
    with pytest.raises(BalanceUnavailable):
        _client(FakeResp(status=500)).diem_balance()

def test_network_error_raises_unavailable():
    with pytest.raises(BalanceUnavailable):
        _client(exc=ConnectionError("down")).diem_balance()

def test_missing_key_raises_unavailable():
    with pytest.raises(BalanceUnavailable):
        _client(FakeResp(body={"data": {}})).diem_balance()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_balance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.balance'`

- [ ] **Step 3: Implement**

```python
# diem/balance.py
"""Live DIEM balance. Spec rule: if this is unreachable, the checkpoint
aborts — never drain blind."""
from __future__ import annotations
import requests

RATE_LIMITS_URL = "https://api.venice.ai/api/v1/api_keys/rate_limits"


class BalanceUnavailable(RuntimeError):
    pass


class BalanceClient:
    def __init__(self, api_key: str, *, get=None, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
        self._get = get or requests.get

    def diem_balance(self) -> float:
        try:
            r = self._get(RATE_LIMITS_URL,
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          timeout=self.timeout)
        except Exception as e:  # noqa: BLE001 — any transport failure = unavailable
            raise BalanceUnavailable(f"rate_limits unreachable: {e}") from e
        if getattr(r, "status_code", 200) != 200:
            raise BalanceUnavailable(f"rate_limits HTTP {r.status_code}")
        try:
            body = r.json()
        except Exception as e:  # noqa: BLE001
            raise BalanceUnavailable(f"rate_limits non-JSON: {e}") from e
        balances = body.get("data", {}).get("balances") or body.get("balances") or {}
        if "DIEM" not in balances:
            raise BalanceUnavailable(f"no DIEM in balances: {body!r:.200}")
        return float(balances["DIEM"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_balance.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/balance.py tests/diem/test_balance.py
git commit -m "feat(diem): DIEM balance client with fail-closed semantics"
```

---

### Task 4: State — estimates, reviewed SHAs, lock, pause

**Files:**
- Create: `diem/state.py`, `tests/diem/test_state.py`

**Interfaces:**
- Consumes: `DiemConfig.seeds` shape from Task 1 (`{type: {"cost": float, "duration_s": float}}`).
- Produces: `Estimates(path: Path, seeds: dict)` with `.estimate(type_) -> tuple[float, float]` (cost, duration_s; falls back to seeds, then `(1.0, 300.0)`) and `.record(type_, cost, duration_s)` (EMA, alpha 0.3, persisted). `Reviewed(path: Path)` with `.get(repo: str) -> str | None`, `.set(repo: str, sha: str)`. `Lock(path: Path)` with `.acquire() -> bool` (stale lock from a dead pid is broken automatically) and `.release()`. `pause_until(state_dir: Path) -> str | None`, `set_pause(state_dir, until_iso: str)`, `clear_pause(state_dir)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_state.py
import json
import os
from diem.state import Estimates, Reviewed, Lock, pause_until, set_pause, clear_pause

SEEDS = {"images": {"cost": 2.0, "duration_s": 180}}

def test_estimates_seed_then_ema(tmp_path):
    e = Estimates(tmp_path / "estimates.json", SEEDS)
    assert e.estimate("images") == (2.0, 180.0)
    assert e.estimate("unknown") == (1.0, 300.0)
    e.record("images", cost=4.0, duration_s=200)
    cost, dur = e.estimate("images")
    assert cost == 2.0 + 0.3 * (4.0 - 2.0)          # 2.6
    assert dur == 180 + 0.3 * (200 - 180)           # 186
    # persisted: fresh instance sees the update
    e2 = Estimates(tmp_path / "estimates.json", SEEDS)
    assert e2.estimate("images") == (cost, dur)

def test_reviewed_roundtrip(tmp_path):
    r = Reviewed(tmp_path / "reviewed.json")
    assert r.get("/r/a") is None
    r.set("/r/a", "abc123")
    assert Reviewed(tmp_path / "reviewed.json").get("/r/a") == "abc123"

def test_lock_excludes_second_holder(tmp_path):
    a, b = Lock(tmp_path / "l"), Lock(tmp_path / "l")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()

def test_lock_breaks_stale_dead_pid(tmp_path):
    (tmp_path / "l").write_text("999999999")  # no such pid
    assert Lock(tmp_path / "l").acquire()

def test_pause_roundtrip(tmp_path):
    assert pause_until(tmp_path) is None
    set_pause(tmp_path, "2026-07-04T01:00:00")
    assert pause_until(tmp_path) == "2026-07-04T01:00:00"
    clear_pause(tmp_path)
    assert pause_until(tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.state'`

- [ ] **Step 3: Implement**

```python
# diem/state.py
from __future__ import annotations
import json
import os
from pathlib import Path

_ALPHA = 0.3
_FALLBACK = (1.0, 300.0)


class Estimates:
    def __init__(self, path: Path, seeds: dict):
        self.path = Path(path)
        self.seeds = seeds
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def estimate(self, type_: str) -> tuple[float, float]:
        if type_ in self.data:
            d = self.data[type_]
            return (d["cost"], d["duration_s"])
        if type_ in self.seeds:
            s = self.seeds[type_]
            return (float(s["cost"]), float(s["duration_s"]))
        return _FALLBACK

    def record(self, type_: str, *, cost: float, duration_s: float) -> None:
        prev_cost, prev_dur = self.estimate(type_)
        self.data[type_] = {
            "cost": prev_cost + _ALPHA * (cost - prev_cost),
            "duration_s": prev_dur + _ALPHA * (duration_s - prev_dur),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))


class Reviewed:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def get(self, repo: str) -> str | None:
        return self.data.get(repo)

    def set(self, repo: str, sha: str) -> None:
        self.data[repo] = sha
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


class Lock:
    def __init__(self, path: Path):
        self.path = Path(path)

    def acquire(self) -> bool:
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
            except ValueError:
                pid = -1
            if pid > 0 and _pid_alive(pid):
                return False
            self.path.unlink(missing_ok=True)  # stale
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return True

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def _pause_path(state_dir: Path) -> Path:
    return Path(state_dir) / "pause"


def pause_until(state_dir: Path) -> str | None:
    try:
        return _pause_path(state_dir).read_text().strip() or None
    except OSError:
        return None


def set_pause(state_dir: Path, until_iso: str) -> None:
    p = _pause_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(until_iso)


def clear_pause(state_dir: Path) -> None:
    _pause_path(state_dir).unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_state.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/state.py tests/diem/test_state.py
git commit -m "feat(diem): estimates EMA, reviewed SHAs, pid lock, pause marker"
```

---

### Task 5: Auto-discovery — hygiene + feedstock

**Files:**
- Create: `diem/discover.py`, `tests/diem/test_discover.py`

**Interfaces:**
- Consumes: `DiemConfig` (Task 1: `.repos`), `QueueDir`/`new_item` (Task 2), `Reviewed` (Task 4).
- Produces: `discover(cfg, queue, reviewed, now_iso, *, day_start_iso: str | None = None, run=subprocess.run) -> list[Item]` — returns the items it actually added (post-dedupe). When `day_start_iso` is given, items whose dedupe key appears in `queue.archived_keys_since(day_start_iso)` are NOT re-added — this is what enforces spec §4's "one review per repo per night" after the first checkpoint has already run and archived a review. Behavior:
  - Per repo: `git rev-parse HEAD` on the default branch. First sighting → record SHA in `reviewed`, queue nothing (baseline). SHA moved → queue `review` item `{"repo", "range": "<old>..<new>", "head": "<new>"}`. Dirty tree (`git status --porcelain` non-empty) → queue `review` item `{"repo", "diff": true}`.
  - Per repo: if `<repo>/.diem/standing-order.json` exists — schema `{"target": int, "candidates_dir": str (repo-relative), "command": [argv...]}` — count plain files in candidates_dir; shortfall > 0 → queue `images` item `{"repo", "count": shortfall, "command": [...]}`. Missing/malformed standing order → skip silently (spec: the drain never invents creative direction).
  - Git failures on a repo → skip that repo, never raise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_discover.py
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from diem.config import DiemConfig
from diem.discover import discover
from diem.queue import QueueDir
from diem.state import Reviewed

NOW = "2026-07-03T21:00:00"

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)

def _mkrepo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "one")
    return repo

def _cfg(tmp_path, repos):
    return DiemConfig(daily_diem=100.0, repos=[Path(r) for r in repos],
                      state_dir=tmp_path / "state")

def _bits(tmp_path):
    return QueueDir(tmp_path / "state"), Reviewed(tmp_path / "state" / "reviewed.json")

def test_first_sighting_baselines_without_review(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert added == [] and rev.get(str(repo)) is not None

def test_new_commits_queue_range_review(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)          # baseline
    old = rev.get(str(repo))
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "two")
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert [i.type for i in added] == ["review"]
    assert added[0].payload["range"].startswith(old)

def test_dirty_tree_queues_diff_review(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    (repo / "x.py").write_text("x = 1\n")
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert [i.payload.get("diff") for i in added] == [True]
    # second discovery same night: deduped
    assert discover(_cfg(tmp_path, [repo]), q, rev, NOW) == []

def test_feedstock_shortfall_queues_images(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    so_dir = repo / ".diem"; so_dir.mkdir()
    cand = repo / "candidates"; cand.mkdir()
    (cand / "t1.png").write_bytes(b"x")
    (so_dir / "standing-order.json").write_text(json.dumps(
        {"target": 4, "candidates_dir": "candidates",
         "command": ["python", "make.py"]}))
    q, rev = _bits(tmp_path)
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    imgs = [i for i in added if i.type == "images"]
    assert len(imgs) == 1 and imgs[0].payload["count"] == 3
    assert imgs[0].payload["command"] == ["python", "make.py"]

def test_no_standing_order_no_images(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    assert all(i.type != "images" for i in discover(_cfg(tmp_path, [repo]), q, rev, NOW))

def test_broken_repo_skipped(tmp_path):
    notrepo = tmp_path / "plain"; notrepo.mkdir()
    q, rev = _bits(tmp_path)
    assert discover(_cfg(tmp_path, [notrepo]), q, rev, NOW) == []

def test_archived_tonight_not_rediscovered(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)          # baseline
    (repo / "x.py").write_text("x = 1\n")
    day = "2026-07-03T01:00:00"
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW, day_start_iso=day)
    assert len(added) == 1
    q.archive(added[0], {"ok": True})                       # ran at 21:00 checkpoint
    # tree still dirty at the 23:00 checkpoint — must NOT re-queue tonight
    assert discover(_cfg(tmp_path, [repo]), q, rev, NOW, day_start_iso=day) == []
    # next DIEM day: eligible again
    assert len(discover(_cfg(tmp_path, [repo]), q, rev,
                        "2026-07-04T21:00:00", day_start_iso="2026-07-04T01:00:00")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_discover.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.discover'`

- [ ] **Step 3: Implement**

```python
# diem/discover.py
"""Checkpoint-time discovery: hygiene (unreviewed commits / dirty trees) and
publishing feedstock (standing-order stock shortfalls). Discovery only ADDS
queue items; QueueDir.add's dedupe makes it idempotent within a night."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from .queue import Item, QueueDir, new_item
from .state import Reviewed


def _git(repo: Path, *args, run) -> str | None:
    try:
        p = run(["git", "-C", str(repo), *args],
                capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 — a broken repo must never kill the drain
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def discover(cfg, queue: QueueDir, reviewed: Reviewed, now_iso: str,
             *, day_start_iso: str | None = None,
             run=subprocess.run) -> list[Item]:
    added: list[Item] = []
    done_tonight = (queue.archived_keys_since(day_start_iso)
                    if day_start_iso else set())

    def _add(item: Item):
        if item.dedupe_key() in done_tonight:
            return  # already ran (or failed out) tonight — once per night
        if queue.add(item):
            added.append(item)

    for repo in cfg.repos:
        repo = Path(repo)
        head = _git(repo, "rev-parse", "HEAD", run=run)
        if head:
            old = reviewed.get(str(repo))
            if old is None:
                reviewed.set(str(repo), head)  # baseline, nothing to review yet
            elif old != head:
                _add(new_item("review", {"repo": str(repo),
                                         "range": f"{old}..{head}",
                                         "head": head}, created=now_iso))
            status = _git(repo, "status", "--porcelain", run=run)
            if status:
                _add(new_item("review", {"repo": str(repo), "diff": True},
                              created=now_iso))

        so_path = repo / ".diem" / "standing-order.json"
        if so_path.exists():
            try:
                so = json.loads(so_path.read_text())
                target = int(so["target"])
                cand = repo / so["candidates_dir"]
                command = list(so["command"])
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue  # malformed standing order: skip, never invent
            stock = sum(1 for f in cand.glob("*") if f.is_file()) if cand.is_dir() else 0
            if stock < target:
                _add(new_item("images", {"repo": str(repo),
                                         "count": target - stock,
                                         "command": command}, created=now_iso))
    return added
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_discover.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/discover.py tests/diem/test_discover.py
git commit -m "feat(diem): hygiene + feedstock auto-discovery"
```

---

### Task 6: Runners — subprocess dispatch per item type

**Files:**
- Create: `diem/runners.py`, `tests/diem/test_runners.py`

**Interfaces:**
- Consumes: `Item` (Task 2), `DiemConfig` (Task 1: `.loom_repo`, `.loom_cmd`, `.cmd_whitelist`, `.outputs_dir`).
- Produces: `RunResult` dataclass (`ok: bool, duration_s: float, output_path: str | None, error: str | None`); `run_item(item, cfg, env: dict, *, deadline_epoch: float, run=subprocess.run, clock=time.monotonic) -> RunResult`. Commands per type (exact argv):
  - `ask`: `council ask <question> --panel <panel> --format md` → stdout saved to `outputs/asks/<id>.md`
  - `review` with `diff`: `council review --diff --format md`, `cwd=repo` → `outputs/reviews/<reponame>-<id>.md`
  - `review` with `range`: first `git -C <repo> diff <range>`, then `council review - --format md` with the diff as stdin → same output path
  - `images` / `cmd`: repo-declared or whitelisted argv (+ `--count N` for images), `cwd=repo`; stdout/stderr captured to `outputs/logs/<id>.log`; the pipeline stages its own artifacts
  - `backfill`: `cfg.loom_cmd + ["--max-targets", str(n)]`, `cwd=cfg.loom_repo` → log file
  - Subprocess `timeout = max(30, deadline_epoch - clock())` — the 00:50 hard backstop. Timeout/nonzero-exit/missing-whitelist → `ok=False` with error text, never an exception.

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_runners.py
import subprocess
from pathlib import Path
from types import SimpleNamespace
from diem.config import DiemConfig
from diem.queue import new_item
from diem.runners import run_item

NOW = "2026-07-03T21:00:00"

class FakeRun:
    """Records subprocess calls; scripted (returncode, stdout) per call."""
    def __init__(self, results=None, raise_timeout=False):
        self.calls = []
        self.results = list(results or [])
        self.raise_timeout = raise_timeout
    def __call__(self, argv, **kw):
        self.calls.append({"argv": argv, **kw})
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        rc, out = self.results.pop(0) if self.results else (0, "ok-output")
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

def _cfg(tmp_path):
    return DiemConfig(daily_diem=100.0, repos=[],
                      state_dir=tmp_path / "state", outputs_dir=tmp_path / "out",
                      loom_repo=tmp_path / "loomrepo",
                      loom_cmd=["python", "-m", "loom.cli", "backfill"],
                      cmd_whitelist={"teasers": {"repo": str(tmp_path / "re"),
                                                 "argv": ["python", "make.py"]}})

def test_ask_invokes_council_and_saves_output(tmp_path):
    fr = FakeRun()
    it = new_item("ask", {"question": "q?", "panel": "decision"}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {"VENICE_API_KEY": "k"},
                   deadline_epoch=10_000.0, run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["council", "ask", "q?", "--panel", "decision",
                                   "--format", "md"]
    assert fr.calls[0]["env"]["VENICE_API_KEY"] == "k"
    assert Path(res.output_path).read_text() == "ok-output"

def test_review_diff_runs_in_repo(tmp_path):
    fr = FakeRun()
    it = new_item("review", {"repo": "/r/swim", "diff": True}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and fr.calls[0]["cwd"] == "/r/swim"
    assert fr.calls[0]["argv"] == ["council", "review", "--diff", "--format", "md"]

def test_review_range_pipes_git_diff_to_stdin(tmp_path):
    fr = FakeRun(results=[(0, "THE DIFF"), (0, "verdict")])
    it = new_item("review", {"repo": "/r/swim", "range": "a..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["git", "-C", "/r/swim", "diff", "a..b"]
    assert fr.calls[1]["argv"] == ["council", "review", "-", "--format", "md"]
    assert fr.calls[1]["input"] == "THE DIFF"

def test_review_range_empty_diff_short_circuits(tmp_path):
    fr = FakeRun(results=[(0, "")])
    it = new_item("review", {"repo": "/r/swim", "range": "a..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and len(fr.calls) == 1  # council never called on empty diff

def test_images_appends_count(tmp_path):
    fr = FakeRun()
    it = new_item("images", {"repo": "/r/re", "count": 5,
                             "command": ["python", "make.py"]}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["python", "make.py", "--count", "5"]
    assert fr.calls[0]["cwd"] == "/r/re"

def test_backfill_uses_loom_cmd(tmp_path):
    fr = FakeRun()
    it = new_item("backfill", {"max_targets": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["python", "-m", "loom.cli", "backfill",
                                   "--max-targets", "2"]

def test_cmd_requires_whitelist(tmp_path):
    fr = FakeRun()
    ok = new_item("cmd", {"name": "teasers"}, created=NOW)
    bad = new_item("cmd", {"name": "rm-rf"}, created=NOW)
    assert run_item(ok, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                    run=fr, clock=lambda: 0.0).ok
    res = run_item(bad, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert not res.ok and "whitelist" in res.error

def test_timeout_and_nonzero_are_failures_not_exceptions(tmp_path):
    it = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=FakeRun(raise_timeout=True), clock=lambda: 0.0)
    assert not res.ok and "timeout" in res.error.lower()
    res2 = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                    run=FakeRun(results=[(2, "boom")]), clock=lambda: 0.0)
    assert not res2.ok and "exit 2" in res2.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_runners.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.runners'`

- [ ] **Step 3: Implement**

```python
# diem/runners.py
"""One runner per item type. diem never implements a workload — it shells
out to council / loom / repo-declared commands, with the 00:50 deadline as
a subprocess hard timeout. Failures return RunResult(ok=False), never raise."""
from __future__ import annotations
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    ok: bool
    duration_s: float
    output_path: str | None = None
    error: str | None = None


def _save(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "")
    return str(path)


def run_item(item, cfg, env: dict, *, deadline_epoch: float,
             run=subprocess.run, clock=time.monotonic) -> RunResult:
    start = clock()
    timeout = max(30.0, deadline_epoch - start)
    p = item.payload

    def _exec(argv, *, cwd=None, input=None):
        return run(argv, capture_output=True, text=True, timeout=timeout,
                   env=env, cwd=cwd, input=input)

    def _done(proc, out_path: Path | None, log_stdout: bool):
        dur = clock() - start
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-500:]
            return RunResult(False, dur, error=f"exit {proc.returncode}: {err}")
        saved = _save(out_path, proc.stdout) if (out_path and log_stdout) else None
        return RunResult(True, dur, output_path=saved)

    try:
        if item.type == "ask":
            proc = _exec(["council", "ask", p["question"],
                          "--panel", p.get("panel", "decision"), "--format", "md"])
            return _done(proc, Path(cfg.outputs_dir) / "asks" / f"{item.id}.md", True)

        if item.type == "review":
            repo = p["repo"]
            name = Path(repo).name
            out = Path(cfg.outputs_dir) / "reviews" / f"{name}-{item.id}.md"
            if p.get("diff"):
                proc = _exec(["council", "review", "--diff", "--format", "md"],
                             cwd=repo)
                return _done(proc, out, True)
            gd = _exec(["git", "-C", repo, "diff", p["range"]])
            if gd.returncode != 0:
                return RunResult(False, clock() - start,
                                 error=f"git diff failed: {gd.stderr.strip()[-300:]}")
            if not gd.stdout.strip():
                return RunResult(True, clock() - start)  # nothing to review
            proc = _exec(["council", "review", "-", "--format", "md"],
                         input=gd.stdout)
            return _done(proc, out, True)

        if item.type == "images":
            argv = list(p["command"]) + ["--count", str(p["count"])]
            proc = _exec(argv, cwd=p["repo"])
            return _done(proc, Path(cfg.outputs_dir) / "logs" / f"{item.id}.log", True)

        if item.type == "backfill":
            argv = list(cfg.loom_cmd) + ["--max-targets", str(p.get("max_targets", 2))]
            proc = _exec(argv, cwd=str(cfg.loom_repo))
            return _done(proc, Path(cfg.outputs_dir) / "logs" / f"{item.id}.log", True)

        if item.type == "cmd":
            entry = cfg.cmd_whitelist.get(p.get("name", ""))
            if not entry:
                return RunResult(False, clock() - start,
                                 error=f"'{p.get('name')}' not in cmd whitelist")
            proc = _exec(list(entry["argv"]), cwd=entry["repo"])
            return _done(proc, Path(cfg.outputs_dir) / "logs" / f"{item.id}.log", True)

        return RunResult(False, clock() - start, error=f"unknown type {item.type}")
    except subprocess.TimeoutExpired:
        return RunResult(False, clock() - start,
                         error=f"timeout after {timeout:.0f}s (deadline backstop)")
    except Exception as e:  # noqa: BLE001 — one bad job must not kill the drain
        return RunResult(False, clock() - start, error=f"{type(e).__name__}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_runners.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/runners.py tests/diem/test_runners.py
git commit -m "feat(diem): subprocess runners with deadline backstop"
```

---

### Task 7: Drain loop

**Files:**
- Create: `diem/drain.py`, `tests/diem/test_drain.py`

**Interfaces:**
- Consumes: everything above — `DiemConfig` (T1), `QueueDir` (T2), `BalanceClient`/`BalanceUnavailable` (T3), `Estimates`/`Reviewed`/`Lock`/`pause_until` (T4), `discover` (T5), `run_item` signature (T6).
- Produces: `run_checkpoint(cfg, *, now, balance, queue, estimates, reviewed, runner, run=subprocess.run) -> dict` where `now: datetime` (aware-naive local), `balance` has `.diem_balance()`, `runner` matches `run_item`'s signature minus cfg/env (the CLI partial-applies those). Returns a summary dict: `{"aborted": str|None, "floor": float, "started_balance": float, "ended_balance": float, "ran": [ {id,type,ok,cost,duration_s,output_path,error} ], "skipped": [...], "deadline": iso}`.
- Also produces module helpers used by CLI/status: `floor_for(cfg, now: datetime) -> float` (fraction × daily_diem of the latest checkpoint at or before `now`, on the DIEM-day clock — a `00:15` checkpoint uses the `00:15` floor, `20:00` with no checkpoint yet uses the *first* checkpoint's floor as a conservative default), `next_deadline(cfg, now) -> datetime`, `next_reset(cfg, now) -> datetime`.

**Drain semantics (the heart — implement exactly):**
1. Lock or return `{"aborted": "locked"}`. Pause active (`pause_until` > now) → `{"aborted": "paused"}`.
2. `discover(...)` first (queue tops up), then loop:
3. Read balance. `BalanceUnavailable` → abort with `"aborted": "balance_unavailable"` (never drain blind). Balance ≤ floor → stop normally.
4. Walk `queue.pending()` in order; pick the first item where `bal - est_cost >= floor` AND `now + est_duration <= deadline`, **skipping any item already attempted this checkpoint** (a failed-and-requeued item retries at the NEXT checkpoint, not in the same loop). Items that don't fit are recorded in `skipped` (reason `budget` or `deadline`) but stay queued.
5. Filler top-up ONLY when the pending queue is completely empty (never when items exist but don't fit — a deadline-skipped review must not spawn backfill noise): if `queue.night_count("backfill", day_start_iso) < cfg.backfill_max_per_night`, enqueue a filler `backfill` item (chunk `cfg.backfill_chunk`) and continue the loop (it gets picked through the normal budget/deadline gate); else stop. `day_start_iso = (next_reset(cfg, now) - 1 day).isoformat()` — also passed to `discover(...)` as `day_start_iso` so per-night dedupe holds across checkpoints.
6. Run item. Re-read balance; `cost = max(0.0, before - after)`. `estimates.record(type, cost, duration)`. Success → archive `{"ok": True, ...}`; review-range success also `reviewed.set(repo, head)`. Failure → `attempts += 1`; re-queue (rewrite file) if `attempts < max_attempts`, else archive `{"ok": False, "error": ...}`.
7. Loop until floor/deadline/queue-exhausted. Always release lock (try/finally).

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_drain.py
from datetime import datetime
from pathlib import Path
import pytest
from diem.config import DiemConfig, Checkpoint
from diem.drain import run_checkpoint, floor_for, next_deadline
from diem.queue import QueueDir, new_item
from diem.runners import RunResult
from diem.state import Estimates, Reviewed, set_pause

NOW = datetime(2026, 7, 3, 23, 5)
NOW_ISO = "2026-07-03T23:05:00"

class FakeBalance:
    """Scripted balance readings; drains by `burn` per run when scripted list empties."""
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = 0
    def diem_balance(self):
        self.calls += 1
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

class FakeRunner:
    def __init__(self, results=None):
        self.ran = []
        self.results = results or {}
    def __call__(self, item, **kw):
        self.ran.append(item)
        return self.results.get(item.id, RunResult(True, 60.0, output_path="/o"))

def _cfg(tmp_path, **kw):
    base = dict(daily_diem=100.0, repos=[], state_dir=tmp_path / "state",
                outputs_dir=tmp_path / "out",
                checkpoints=[Checkpoint("21:00", 0.40), Checkpoint("23:00", 0.15),
                             Checkpoint("00:15", 0.0)])
    base.update(kw)
    return DiemConfig(**base)

def _bits(tmp_path, cfg):
    q = QueueDir(cfg.state_dir)
    return (q, Estimates(cfg.state_dir / "estimates.json", cfg.seeds),
            Reviewed(cfg.state_dir / "reviewed.json"))

def test_floor_for_picks_latest_checkpoint():
    cfg = _cfg(Path("/tmp/x"))
    assert floor_for(cfg, datetime(2026, 7, 3, 21, 30)) == 40.0
    assert floor_for(cfg, datetime(2026, 7, 3, 23, 5)) == 15.0
    assert floor_for(cfg, datetime(2026, 7, 4, 0, 20)) == 0.0
    assert floor_for(cfg, datetime(2026, 7, 3, 20, 0)) == 40.0  # pre-first: conservative
    # after midnight but before 00:15, last-fired checkpoint is yesterday 23:00
    assert floor_for(cfg, datetime(2026, 7, 4, 0, 5)) == 15.0

def test_next_deadline_before_and_after_midnight():
    cfg = _cfg(Path("/tmp/x"))
    assert next_deadline(cfg, datetime(2026, 7, 3, 23, 5)) == datetime(2026, 7, 4, 0, 50)
    assert next_deadline(cfg, datetime(2026, 7, 4, 0, 20)) == datetime(2026, 7, 4, 0, 50)

def test_drains_until_floor(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    for n in range(3):
        q.add(new_item("ask", {"question": f"q{n}", "panel": "decision"}, created=NOW_ISO))
    # floor at 23:05 = 15.0; readings: 40 → run → 25 → run → 14 (≤ floor, stop)
    bal = FakeBalance([40.0, 25.0, 25.0, 14.0, 14.0])
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=NOW, balance=bal, queue=q,
                             estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 2 and summary["aborted"] is None
    assert len(q.pending(NOW_ISO)) == 1  # third ask survives for the 00:15 pass

def test_balance_unavailable_aborts(tmp_path):
    from diem.balance import BalanceUnavailable
    class Down:
        def diem_balance(self):
            raise BalanceUnavailable("nope")
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    summary = run_checkpoint(cfg, now=NOW, balance=Down(), queue=q,
                             estimates=est, reviewed=rev, runner=FakeRunner())
    assert summary["aborted"] == "balance_unavailable"
    assert len(q.pending(NOW_ISO)) == 1  # nothing consumed

def test_deadline_skips_long_jobs(tmp_path):
    cfg = _cfg(tmp_path, seeds={"images": {"cost": 1.0, "duration_s": 3600},
                                "ask": {"cost": 1.0, "duration_s": 60}})
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("images", {"repo": "/r", "count": 9,
                              "command": ["x"]}, created=NOW_ISO))
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    late = datetime(2026, 7, 4, 0, 30)  # 20 min to 00:50 deadline
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=late, balance=FakeBalance([50.0, 40.0, 40.0]),
                             queue=q, estimates=est, reviewed=rev, runner=r)
    assert [i.type for i in r.ran] == ["ask"]  # images (60 min est) skipped
    assert any(s["reason"] == "deadline" for s in summary["skipped"])

def test_paused_aborts(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    set_pause(cfg.state_dir, "2026-07-04T01:00:00")
    summary = run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0]), queue=q,
                             estimates=est, reviewed=rev, runner=FakeRunner())
    assert summary["aborted"] == "paused"

def test_failure_requeues_then_archives(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    it = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO)
    q.add(it)
    fail = FakeRunner({it.id: RunResult(False, 5.0, error="exit 2: boom")})
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 50.0, 50.0]), queue=q,
                   estimates=est, reviewed=rev, runner=fail)
    pend = q.pending(NOW_ISO)
    assert len(pend) == 1 and pend[0].attempts == 1  # requeued once
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 50.0, 50.0]), queue=q,
                   estimates=est, reviewed=rev, runner=fail)
    assert q.pending(NOW_ISO) == []  # attempts == max_attempts → archived failed

def test_review_range_success_advances_reviewed_sha(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    rev.set("/r/a", "old")
    q.add(new_item("review", {"repo": "/r/a", "range": "old..new", "head": "new"},
                   created=NOW_ISO))
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 49.0, 49.0]), queue=q,
                   estimates=est, reviewed=rev, runner=FakeRunner())
    assert rev.get("/r/a") == "new"

def test_filler_backfill_tops_up_empty_queue(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=2, backfill_chunk=3)
    q, est, rev = _bits(tmp_path, cfg)
    r = FakeRunner()
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 40.0, 30.0, 20.0, 14.0]),
                   queue=q, estimates=est, reviewed=rev, runner=r)
    assert 1 <= len(r.ran) <= 2
    assert all(i.type == "backfill" and i.payload["max_targets"] == 3 for i in r.ran)
    assert q.night_count("backfill", "2026-07-03T01:00:00") <= 2  # cap respected

def test_estimates_recorded_from_balance_delta(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 47.0, 14.0]), queue=q,
                   estimates=est, reviewed=rev, runner=FakeRunner())
    cost, _dur = est.estimate("ask")
    assert cost == pytest.approx(0.5 + 0.3 * (3.0 - 0.5))  # EMA toward observed 3.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_drain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.drain'`

- [ ] **Step 3: Implement**

```python
# diem/drain.py
"""The drain loop. All decisions are clock/budget arithmetic — no judgment.
Interactive evening use is honored implicitly: balance re-read between jobs
means a human burning DIEM pushes the balance to the floor and we stop."""
from __future__ import annotations
import subprocess
from datetime import datetime, timedelta

from .balance import BalanceUnavailable
from .discover import discover
from .queue import new_item
from .state import Lock, pause_until


def _at(now: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def next_deadline(cfg, now: datetime) -> datetime:
    d = _at(now, cfg.deadline)
    return d if now <= d else d + timedelta(days=1)


def next_reset(cfg, now: datetime) -> datetime:
    r = _at(now, cfg.reset)
    return r if now <= r else r + timedelta(days=1)


def floor_for(cfg, now: datetime) -> float:
    """Latest checkpoint at-or-before now on the DIEM day (reset..reset).
    Checkpoint times are anchored to the DAY START, not now's date — at
    00:05 the operative checkpoint is *yesterday's* 23:00, and a 00:15
    checkpoint belongs to the day that started the previous 01:00.
    Before the first checkpoint fires, use the first (most conservative)."""
    day_start = next_reset(cfg, now) - timedelta(days=1)
    best = None
    for cp in cfg.checkpoints:
        t = _at(day_start, cp.time)
        if t < day_start:
            t += timedelta(days=1)
        if t <= now and (best is None or t > best[0]):
            best = (t, cp.floor)
    frac = best[1] if best else cfg.checkpoints[0].floor
    return frac * cfg.daily_diem


def run_checkpoint(cfg, *, now: datetime, balance, queue, estimates, reviewed,
                   runner, run=subprocess.run) -> dict:
    now_iso = now.isoformat(timespec="seconds")
    floor = floor_for(cfg, now)
    deadline = next_deadline(cfg, now)
    summary = {"aborted": None, "floor": floor, "started_balance": None,
               "ended_balance": None, "ran": [], "skipped": [],
               "deadline": deadline.isoformat(timespec="seconds")}

    pu = pause_until(cfg.state_dir)
    if pu and pu > now_iso:
        summary["aborted"] = "paused"
        return summary

    lock = Lock(cfg.state_dir / "drain.lock")
    if not lock.acquire():
        summary["aborted"] = "locked"
        return summary
    try:
        day_start_iso = (next_reset(cfg, now) - timedelta(days=1)) \
            .isoformat(timespec="seconds")
        discover(cfg, queue, reviewed, now_iso, day_start_iso=day_start_iso,
                 run=run)
        elapsed = 0.0    # simulated wall-clock from job durations (tests inject now)
        attempted = set()  # ids run this checkpoint — failures retry NEXT checkpoint
        while True:
            try:
                bal = balance.diem_balance()
            except BalanceUnavailable:
                summary["aborted"] = "balance_unavailable"
                return summary
            if summary["started_balance"] is None:
                summary["started_balance"] = bal
            summary["ended_balance"] = bal
            if bal <= floor:
                return summary

            eff_now = now + timedelta(seconds=elapsed)
            pend = queue.pending(now_iso)
            picked, skipped_this_pass = None, []
            for it in pend:
                if it.id in attempted:
                    continue
                cost, dur = estimates.estimate(it.type)
                if bal - cost < floor:
                    skipped_this_pass.append({"id": it.id, "type": it.type,
                                              "reason": "budget"})
                elif eff_now + timedelta(seconds=dur) > deadline:
                    skipped_this_pass.append({"id": it.id, "type": it.type,
                                              "reason": "deadline"})
                else:
                    picked = it
                    break
            summary["skipped"].extend(skipped_this_pass)

            if picked is None:
                # Filler ONLY on a truly empty queue — items that merely don't
                # fit (budget/deadline) must not spawn backfill noise.
                if (not pend and queue.night_count("backfill", day_start_iso)
                        < cfg.backfill_max_per_night):
                    queue.add(new_item("backfill",
                                       {"max_targets": cfg.backfill_chunk},
                                       created=now_iso))
                    continue  # picked up through the normal budget/deadline gate
                return summary

            attempted.add(picked.id)
            deadline_epoch = (deadline - eff_now).total_seconds()
            res = runner(picked, deadline_epoch=deadline_epoch)
            elapsed += res.duration_s
            try:
                after = balance.diem_balance()
            except BalanceUnavailable:
                after = bal
            cost = max(0.0, bal - after)
            estimates.record(picked.type, cost=cost, duration_s=res.duration_s)
            entry = {"id": picked.id, "type": picked.type, "ok": res.ok,
                     "cost": cost, "duration_s": res.duration_s,
                     "output_path": res.output_path, "error": res.error}
            summary["ran"].append(entry)
            if res.ok:
                queue.archive(picked, {"ok": True, "cost": cost,
                                       "output_path": res.output_path})
                if picked.type == "review" and picked.payload.get("head"):
                    reviewed.set(picked.payload["repo"], picked.payload["head"])
            else:
                picked.attempts += 1
                if picked.attempts < picked.max_attempts:
                    queue.requeue(picked)
                else:
                    queue.archive(picked, {"ok": False, "error": res.error})
    finally:
        lock.release()
```

Note for the implementer: `runner` is called as `runner(item, deadline_epoch=...)`. In the CLI (Task 9) the real partial is `lambda item, deadline_epoch: run_item(item, cfg, env, deadline_epoch=time.monotonic() + deadline_epoch)` — the drain passes *seconds remaining*, the CLI converts to a monotonic epoch. Keep that contract; the tests above pin it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_drain.py -v`
Expected: 10 PASS

- [ ] **Step 5: Run the whole diem suite**

Run: `python -m pytest tests/diem -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add diem/drain.py tests/diem/test_drain.py
git commit -m "feat(diem): checkpoint drain loop — floor/deadline math, yield, filler, requeue"
```

---

### Task 8: Reporting — morning report, evening ping, Telegram

**Files:**
- Create: `diem/report.py`, `tests/diem/test_report.py`

**Interfaces:**
- Consumes: the `summary` dict shape produced by `run_checkpoint` (Task 7).
- Produces: `evening_ping(summary, cfg) -> str` (one line: balance %, queue depth, plan); `write_morning_report(cfg, date_str, summaries: list[dict]) -> Path` (writes `<state_dir>/reports/<date>.md`, returns path); `send_telegram(cfg, text, *, post=requests.post) -> bool` (POST `https://api.telegram.org/bot<token>/sendMessage` with `{"chat_id", "text"}`; returns False — never raises — on any failure or when `cfg.telegram` is None).

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_report.py
from types import SimpleNamespace
from diem.config import DiemConfig
from diem.report import evening_ping, send_telegram, write_morning_report

SUMMARY = {"aborted": None, "floor": 15.0, "started_balance": 40.0,
           "ended_balance": 12.0, "deadline": "2026-07-04T00:50:00",
           "ran": [{"id": "a1", "type": "review", "ok": True, "cost": 2.0,
                    "duration_s": 120.0, "output_path": "/o/r.md", "error": None},
                   {"id": "b2", "type": "images", "ok": False, "cost": 0.0,
                    "duration_s": 5.0, "output_path": None, "error": "exit 2: x"}],
           "skipped": [{"id": "c3", "type": "backfill", "reason": "deadline"}]}

def _cfg(tmp_path, telegram=None):
    return DiemConfig(daily_diem=100.0, repos=[], state_dir=tmp_path / "state",
                      telegram=telegram)

def test_evening_ping_one_line(tmp_path):
    line = evening_ping(SUMMARY, _cfg(tmp_path))
    assert "\n" not in line and "12" in line  # ended balance visible

def test_morning_report_contents(tmp_path):
    cfg = _cfg(tmp_path)
    path = write_morning_report(cfg, "2026-07-04", [SUMMARY])
    text = path.read_text()
    assert path.name == "2026-07-04.md"
    assert "/o/r.md" in text            # output linked
    assert "exit 2: x" in text          # failure surfaced
    assert "deadline" in text           # skips surfaced

def test_send_telegram_posts(tmp_path):
    calls = []
    def post(url, json=None, timeout=None):
        calls.append((url, json))
        return SimpleNamespace(status_code=200)
    ok = send_telegram(_cfg(tmp_path, {"bot_token": "T", "chat_id": "9"}),
                       "hi", post=post)
    assert ok and calls[0][0] == "https://api.telegram.org/botT/sendMessage"
    assert calls[0][1] == {"chat_id": "9", "text": "hi"}

def test_send_telegram_unconfigured_or_failing_is_quiet(tmp_path):
    assert send_telegram(_cfg(tmp_path), "hi") is False
    def post(url, json=None, timeout=None):
        raise ConnectionError("down")
    assert send_telegram(_cfg(tmp_path, {"bot_token": "T", "chat_id": "9"}),
                         "hi", post=post) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.report'`

- [ ] **Step 3: Implement**

```python
# diem/report.py
from __future__ import annotations
from pathlib import Path
import requests


def evening_ping(summary: dict, cfg) -> str:
    bal = summary.get("ended_balance")
    pct = f"{100 * bal / cfg.daily_diem:.0f}%" if bal is not None else "?"
    ran = summary.get("ran", [])
    return (f"DIEM {pct} left · ran {len(ran)} job(s) "
            f"({sum(1 for r in ran if not r['ok'])} failed) · "
            f"{len(summary.get('skipped', []))} skipped · floor {summary['floor']:.0f}")


def write_morning_report(cfg, date_str: str, summaries: list[dict]) -> Path:
    out = Path(cfg.state_dir) / "reports" / f"{date_str}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# DIEM drain report — {date_str}", ""]
    for s in summaries:
        lines.append(f"## Checkpoint (floor {s['floor']:.0f}, "
                     f"deadline {s['deadline']})")
        if s.get("aborted"):
            lines.append(f"- **aborted:** {s['aborted']}")
        lines.append(f"- balance {s.get('started_balance')} → {s.get('ended_balance')}")
        for r in s.get("ran", []):
            mark = "ok" if r["ok"] else f"FAILED — {r['error']}"
            link = f" → `{r['output_path']}`" if r.get("output_path") else ""
            lines.append(f"- {r['type']} `{r['id'][:8]}`: {mark} "
                         f"(cost {r['cost']:.2f}, {r['duration_s']:.0f}s){link}")
        for sk in s.get("skipped", []):
            lines.append(f"- skipped {sk['type']} `{sk['id'][:8]}` ({sk['reason']})")
        lines.append("")
    out.write_text("\n".join(lines))
    return out


def send_telegram(cfg, text: str, *, post=requests.post) -> bool:
    tg = cfg.telegram
    if not tg or not tg.get("bot_token") or not tg.get("chat_id"):
        return False
    try:
        r = post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                 json={"chat_id": tg["chat_id"], "text": text}, timeout=30)
        return getattr(r, "status_code", 0) == 200
    except Exception:  # noqa: BLE001 — reporting must never break the drain
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/diem/test_report.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add diem/report.py tests/diem/test_report.py
git commit -m "feat(diem): morning report, evening ping, telegram sender"
```

---

### Task 9: CLI

**Files:**
- Create: `diem/cli.py`, `tests/diem/test_cli.py`

**Interfaces:**
- Consumes: all modules above.
- Produces: `main(argv=None) -> int` wired as console script `diem`. Subcommands:
  - `diem drain --checkpoint` — load config+key, build real collaborators, `run_checkpoint`, append summary to `<state_dir>/summaries/<date>.jsonl`; at the **first** checkpoint of the night send `evening_ping` to Telegram; if `now` is past the **last** configured checkpoint time, also `write_morning_report` from the night's jsonl and send the 3-line Telegram summary. `--config PATH` override for tests/ops.
  - `diem status` — balance, % of `daily_diem`, time to reset/deadline, queue depth (banked/discovered), current floor. Exit 0.
  - `diem queue add ask "Q" [--panel P] [--expires ISO]` / `queue add review REPO [--range A..B]` / `queue add images REPO COUNT` / `queue add backfill [--max-targets N]` / `queue add cmd NAME` — all banked=True.
  - `diem queue list` (id · type · banked · created · summary), `diem queue rm ID`.
  - `diem pause [HOURS]` (no arg → until next reset), `diem resume`.
- **The date used for jsonl/report filenames is the DIEM-day label:** `(next_reset(cfg, now) - 1 day).date()` at drain time — so the 00:15 checkpoint lands in the same night's file as 21:00/23:00.

- [ ] **Step 1: Write the failing tests**

```python
# tests/diem/test_cli.py
import json
from pathlib import Path
import pytest
import diem.cli as cli
from diem.config import DiemConfig
from diem.queue import QueueDir

def _cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f'daily_diem = 100.0\nrepos = []\n'
                 f'state_dir = "{tmp_path / "state"}"\n'
                 f'outputs_dir = "{tmp_path / "out"}"\n')
    return p

def test_queue_add_and_list_and_rm(tmp_path, capsys, monkeypatch):
    cfgp = _cfg_file(tmp_path)
    assert cli.main(["queue", "add", "ask", "which host?", "--panel", "decision",
                     "--config", str(cfgp)]) == 0
    q = QueueDir(tmp_path / "state")
    items = q.pending("2026-07-03T21:00:00")
    assert len(items) == 1 and items[0].banked and items[0].type == "ask"
    assert cli.main(["queue", "list", "--config", str(cfgp)]) == 0
    out = capsys.readouterr().out
    assert "ask" in out and items[0].id[:8] in out
    assert cli.main(["queue", "rm", items[0].id, "--config", str(cfgp)]) == 0
    assert q.pending("2026-07-03T21:00:00") == []

def test_queue_add_review_and_images(tmp_path):
    cfgp = _cfg_file(tmp_path)
    cli.main(["queue", "add", "review", "/r/swim", "--config", str(cfgp)])
    cli.main(["queue", "add", "images", "/r/re", "7", "--config", str(cfgp)])
    q = QueueDir(tmp_path / "state")
    by_type = {i.type: i for i in q.pending("2026-07-03T21:00:00")}
    assert by_type["review"].payload == {"repo": "/r/swim", "diff": True}
    assert by_type["images"].payload["count"] == 7

def test_pause_and_resume(tmp_path):
    cfgp = _cfg_file(tmp_path)
    from diem.state import pause_until
    assert cli.main(["pause", "2", "--config", str(cfgp)]) == 0
    assert pause_until(tmp_path / "state") is not None
    assert cli.main(["resume", "--config", str(cfgp)]) == 0
    assert pause_until(tmp_path / "state") is None

def test_drain_requires_checkpoint_flag(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["drain", "--config", str(_cfg_file(tmp_path))])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/diem/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diem.cli'` (or AttributeError)

- [ ] **Step 3: Implement**

```python
# diem/cli.py
"""`diem` console entry. Cron calls `diem drain --checkpoint`; humans and
Claude sessions use queue/status/pause. Config: ~/.config/diem/config.toml."""
from __future__ import annotations
import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .balance import BalanceClient
from .config import DiemConfig, load_venice_key
from .drain import floor_for, next_deadline, next_reset, run_checkpoint
from .queue import QueueDir, new_item
from .report import evening_ping, send_telegram, write_morning_report
from .runners import run_item
from .state import Estimates, Reviewed, clear_pause, set_pause
from .queue import Item  # noqa: F401 (re-export convenience for sessions)


def _now() -> datetime:
    return datetime.now()


def _bits(cfg):
    q = QueueDir(cfg.state_dir)
    est = Estimates(Path(cfg.state_dir) / "estimates.json", cfg.seeds)
    rev = Reviewed(Path(cfg.state_dir) / "reviewed.json")
    return q, est, rev


def _diem_day(cfg, now: datetime) -> str:
    return (next_reset(cfg, now) - timedelta(days=1)).date().isoformat()


def _cmd_drain(cfg, now: datetime) -> int:
    key = load_venice_key()
    env = {"VENICE_API_KEY": key, "VENICE_KEY": key,
           "PATH": "/home/dev/.local/bin:/usr/local/bin:/usr/bin:/bin",
           "HOME": str(Path.home())}
    q, est, rev = _bits(cfg)
    balance = BalanceClient(key)

    def runner(item, *, deadline_epoch):
        return run_item(item, cfg, env,
                        deadline_epoch=time.monotonic() + deadline_epoch)

    summary = run_checkpoint(cfg, now=now, balance=balance, queue=q,
                             estimates=est, reviewed=rev, runner=runner)
    day = _diem_day(cfg, now)
    jl = Path(cfg.state_dir) / "summaries" / f"{day}.jsonl"
    jl.parent.mkdir(parents=True, exist_ok=True)
    first_of_night = not jl.exists()
    with open(jl, "a") as fh:
        fh.write(json.dumps(summary) + "\n")

    if first_of_night:
        send_telegram(cfg, evening_ping(summary, cfg))
    last_cp = max(cfg.checkpoints,
                  key=lambda c: (c.time < cfg.reset, c.time))  # 00:15 sorts last
    if now.strftime("%H:%M") >= last_cp.time and now.strftime("%H:%M") < cfg.reset:
        summaries = [json.loads(l) for l in jl.read_text().splitlines() if l.strip()]
        path = write_morning_report(cfg, day, summaries)
        ran = sum(len(s.get("ran", [])) for s in summaries)
        failed = sum(1 for s in summaries for r in s.get("ran", []) if not r["ok"])
        send_telegram(cfg, f"DIEM night done: {ran} job(s), {failed} failed.\n"
                           f"Report: {path}")
    print(json.dumps(summary, indent=1))
    return 0


def _cmd_status(cfg, now: datetime) -> int:
    q, est, _ = _bits(cfg)
    try:
        bal = BalanceClient(load_venice_key()).diem_balance()
        pct = f"{100 * bal / cfg.daily_diem:.0f}%"
    except SystemExit:
        bal, pct = None, "? (no key)"
    pend = q.pending(now.isoformat(timespec="seconds"))
    banked = sum(1 for i in pend if i.banked)
    print(f"balance:  {bal} ({pct} of {cfg.daily_diem})")
    print(f"floor:    {floor_for(cfg, now):.1f}  deadline: {next_deadline(cfg, now)}"
          f"  reset: {next_reset(cfg, now)}")
    print(f"queue:    {len(pend)} pending ({banked} banked)")
    for i in pend:
        print(f"  {i.id[:8]} {'B' if i.banked else ' '} {i.type:9} {i.created}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="diem")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("drain")
    d.add_argument("--checkpoint", action="store_true", required=True)
    d.add_argument("--config", default=None)

    st = sub.add_parser("status"); st.add_argument("--config", default=None)

    qp = sub.add_parser("queue"); qsub = qp.add_subparsers(dest="qcmd", required=True)
    qa = qsub.add_parser("add"); qa.add_argument("--config", default=None)
    qa.add_argument("type", choices=["ask", "review", "images", "backfill", "cmd"])
    qa.add_argument("args", nargs="*")
    qa.add_argument("--panel", default="decision"); qa.add_argument("--range")
    qa.add_argument("--expires"); qa.add_argument("--max-targets", type=int, default=2)
    ql = qsub.add_parser("list"); ql.add_argument("--config", default=None)
    qr = qsub.add_parser("rm"); qr.add_argument("id"); qr.add_argument("--config", default=None)

    pa = sub.add_parser("pause")
    pa.add_argument("hours", nargs="?", type=float); pa.add_argument("--config", default=None)
    re_ = sub.add_parser("resume"); re_.add_argument("--config", default=None)

    args = p.parse_args(argv)
    cfg = DiemConfig.load(Path(args.config) if args.config else None)
    now = _now()
    now_iso = now.isoformat(timespec="seconds")

    if args.cmd == "drain":
        return _cmd_drain(cfg, now)
    if args.cmd == "status":
        return _cmd_status(cfg, now)
    if args.cmd == "queue":
        q, _, _ = _bits(cfg)
        if args.qcmd == "add":
            payload = None
            if args.type == "ask":
                payload = {"question": " ".join(args.args), "panel": args.panel}
            elif args.type == "review":
                repo = args.args[0]
                payload = ({"repo": repo, "range": args.range,
                            "head": args.range.split("..")[-1]} if args.range
                           else {"repo": repo, "diff": True})
            elif args.type == "images":
                payload = {"repo": args.args[0], "count": int(args.args[1])}
            elif args.type == "backfill":
                payload = {"max_targets": args.max_targets}
            elif args.type == "cmd":
                payload = {"name": args.args[0]}
            it = new_item(args.type, payload, banked=True,
                          expires=args.expires, created=now_iso)
            added = q.add(it)
            print(it.id if added else "duplicate — not added")
            return 0 if added else 1
        if args.qcmd == "list":
            for i in q.pending(now_iso):
                print(f"{i.id[:8]} {'B' if i.banked else ' '} {i.type:9} "
                      f"{i.created}  {json.dumps(i.payload)[:60]}")
            return 0
        if args.qcmd == "rm":
            return 0 if q.remove(args.id) else 1
    if args.cmd == "pause":
        until = (now + timedelta(hours=args.hours)) if args.hours \
            else next_reset(cfg, now)
        set_pause(cfg.state_dir, until.isoformat(timespec="seconds"))
        print(f"paused until {until}")
        return 0
    if args.cmd == "resume":
        clear_pause(cfg.state_dir)
        print("resumed")
        return 0
    return 1
```

Implementation note on `images` banked via CLI: a hand-banked images item has no `command` in its payload; the runner needs one. Extend `run_item`'s images branch (Task 6 file) to fall back to the repo's standing-order command when `payload["command"]` is absent:

```python
        if item.type == "images":
            command = p.get("command")
            if not command:
                import json as _json
                so = Path(p["repo"]) / ".diem" / "standing-order.json"
                try:
                    command = _json.loads(so.read_text())["command"]
                except (OSError, KeyError, ValueError):
                    return RunResult(False, clock() - start,
                                     error="images item has no command and no standing order")
            argv = list(command) + ["--count", str(p["count"])]
```

Add a regression test for that fallback in `tests/diem/test_runners.py`:

```python
def test_images_falls_back_to_standing_order(tmp_path):
    repo = tmp_path / "re"; (repo / ".diem").mkdir(parents=True)
    (repo / ".diem" / "standing-order.json").write_text(
        '{"target": 9, "candidates_dir": "c", "command": ["python", "so.py"]}')
    fr = FakeRun()
    it = new_item("images", {"repo": str(repo), "count": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and fr.calls[0]["argv"] == ["python", "so.py", "--count", "2"]
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/diem -v && python -m pytest tests/ -q`
Expected: all diem tests PASS; full existing suite still green.

- [ ] **Step 5: Commit**

```bash
git add diem/cli.py diem/runners.py tests/diem/test_cli.py tests/diem/test_runners.py
git commit -m "feat(diem): CLI — drain/status/queue/pause + images standing-order fallback"
```

---

### Task 10: Ops doc, install, live smoke test, rollout

**Files:**
- Create: `diem/README.md`
- Create: `~/.config/diem/config.toml` (operator machine, not committed)

**Interfaces:** consumes everything; produces the running system.

- [ ] **Step 1: Write `diem/README.md`**

Content: what it is (2 paragraphs, link to spec), config reference (every key with the Task 1 defaults), the queue-file banking contract for Claude sessions, the three crontab lines, `diem pause/resume/status` ops notes, and the DIEM-day/deadline semantics table from the spec.

- [ ] **Step 2: Reinstall and verify the console script**

Run: `pipx reinstall council 2>/dev/null || pipx install /home/dev/projects/build-ai-automation-workflow --force`
Then: `diem --help` (from a fresh shell)
Expected: usage text with drain/status/queue/pause/resume. (pipx installs from the repo path it was originally installed from — since this branch isn't merged yet, expect this step to run against the *branch* checkout; that's fine for smoke-testing, and Task 10 re-runs after merge.)

- [ ] **Step 3: Write the operator config**

Create `~/.config/diem/config.toml` with real values: `daily_diem` = the actual daily DIEM allowance (ASK THE OPERATOR — do not guess), `repos` = the fleet list (start with 3–4: swimtrack, romance-empire, flight-7-publishing, this repo), telegram bot_token/chat_id from the existing bot.

- [ ] **Step 4: Live smoke test (cheap, reversible)**

```bash
diem status                          # balance parses? floor/deadline sane?
diem queue add ask "Reply with the single word PONG." --panel decision
diem drain --checkpoint              # runs the one banked ask, prints summary
```
Expected: `status` shows a real balance (this validates the rate_limits envelope assumption from Task 3 — **if the shape differs, fix `balance.py` + its test now**); drain runs 1 job; `outputs/asks/<id>.md` contains a council answer; summary jsonl written. If run before 21:00, floor = 40% — if balance is below the floor the drain correctly runs nothing; temporarily use a test config with `floor = 0.9` checkpoints instead of waiting for night.

- [ ] **Step 5: Full suite + council review**

```bash
python -m pytest tests/ -q
set -a; . ~/.env; set +a
council review --diff   # on the branch vs main: use `git diff main... | council review -`
```
Expected: suite green; council findings triaged (fix real ones, note dismissals).

- [ ] **Step 6: Commit README + any review fixes**

```bash
git add diem/README.md
git commit -m "docs(diem): ops guide + crontab lines"
```

- [ ] **Step 7: Merge recommendation (STOP — protocol gate)**

Post per the global merge protocol and STOP:

```
**Merge recommendation — diem-drain-engine → main**
What:     diem CLI: nightly DIEM drain (queue/discover/drain/report) + cron checkpoints
Verified: <N> unit tests green, full suite green, live smoke test (status + 1-job drain), council review
Risk:     new package only — no changes to council/loom behavior; cron not yet installed
How:      merge-commit, delete branch? y
→ say "do it" to merge.
```

- [ ] **Step 8: AFTER approved merge only — install cron**

Propose these lines, get explicit approval, then install via `crontab -e` equivalent:

```cron
0 21 * * *  /home/dev/.local/bin/diem drain --checkpoint >> /home/dev/.local/state/diem/drain.log 2>&1
0 23 * * *  /home/dev/.local/bin/diem drain --checkpoint >> /home/dev/.local/state/diem/drain.log 2>&1
15 0 * * *  /home/dev/.local/bin/diem drain --checkpoint >> /home/dev/.local/state/diem/drain.log 2>&1
```

Then `pipx reinstall council` once more from merged main.

---

## Amendment 2026-07-04 (post Task-7 review — Opus reviewer finding)

The reference `next_deadline` returned the *next occurrence* of 00:50, so a
checkpoint firing in the (00:50, 01:00) gap — or after the reset — saw a
deadline ~24h out at floor 0 and could drain the NEXT day's budget unattended.
Shipped fix (drain.py): `next_deadline` is anchored to the current DIEM day
(`_at(next_reset(cfg, now), cfg.deadline)`, may legitimately be in the past);
`run_checkpoint` aborts with `"past_deadline"` when `now > deadline` and with
`"no_checkpoint_fired"` when no checkpoint of the current DIEM day has fired
yet (kills post-reset and mid-day off-schedule drains; scheduled 21:00/23:00/
00:15 runs are unaffected). Also: `skipped` entries deduped by item id.
Config contract noted: `deadline` must fall between the last checkpoint and
`reset` on the clock (00:50 < 01:00).

## Self-Review Notes (already applied)

- **Spec coverage check:** spec §2 config/state ↔ T1/T4; §3 queue ↔ T2; §4 discovery ↔ T5; §5 scheduling ↔ T7; §6 safety ↔ T6 (whitelist, no publish verbs anywhere) — *except* image content-violation quarantine (spec §6): the standing-order pipeline owns its own staging, and its command is the only thing that sees Venice image headers, so quarantine lives in the pipeline, not diem. This is a deliberate boundary call consistent with "diem never implements workloads"; noted here so the reviewer sees it. §7 reporting ↔ T8/T9; §8 errors ↔ T6/T7 (429 backoff is council's own retry layer — diem does not duplicate it; requeue-once covers the rest); §9 rollout ↔ T10.
- **Type consistency:** `runner(item, deadline_epoch=seconds_remaining)` contract pinned in T7 tests and note; `RunResult` fields identical in T6/T7/T8; `Item` fields identical in T2/T5/T7/T9; summary dict shape identical in T7 (producer) and T8/T9 (consumers).
- **Known simplifications vs spec, on purpose:** uuid4 not ulid (global constraints); balance-delta cost attribution is noisy under concurrent interactive use — EMA absorbs it; `elapsed` wall-clock is advanced by reported job durations rather than re-reading a clock (deterministic under test, accurate enough for deadline math).

