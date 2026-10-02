# Venice Phase D — Per-Project Key Partitioning + Reporter DIEM Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every project its own Venice API key so per-key billing actually attributes spend to the right project, and fix the reconciler so it compares comparable quantities.

**Architecture:** Every Venice consumer currently reads a *generic* env var (`VENICE_API_KEY` or `VENICE_INFERENCE_KEY`) from the ambient environment, which `~/.zshenv` populates from `~/.env` for every shell. Four consumers share `VENICE_API_KEY` and two share `VENICE_INFERENCE_KEY`, so per-project keys are impossible without a code change. Each consumer gains a **uniquely-named project var checked first, with the existing generic name as fallback**. The fallback makes every code change safe to deploy *before* the keys exist — nothing breaks, traffic simply keeps flowing to `DEFAULT` until the new var is set.

**Tech Stack:** Python 3.12 (stdlib `os.environ`), Node 22 (`process.env`), pytest, vitest.

## Global Constraints

- **Never print a secret value, put one on a command line, or leave one in shell history.** Key values reach disk only via a silent `read -rs` prompt. This applies to every ops step.
- Key *names*, *caps*, *usage figures*, and `last6Chars` are not secrets and may be printed.
- **Venice key `description` must equal the ledger `project` tag exactly** — the reconciler maps key→project by that string (stripping an optional `proj-` prefix). The seven canonical tags are: `council`, `loom`, `romance`, `swimtrack`, `swimtrack-coach`, `swimtrack-website`, `venice-ai-skill`.
- **Every inference key is capped `usd: 0`** (decided 2026-07-20). `0` is an enforced zero-dollar cap, not "unset"; `null` means no limit. This makes real-dollar spend structurally impossible — work runs on the 31/day DIEM allowance and *fails* rather than billing when DIEM is exhausted.
- Fallback chains are mandatory on every key lookup: new project var first, existing generic var second. Empty-string values must fall through, not be returned.
- Test suites must never touch the real ledger — every repo already has an isolation guard; do not remove it.
- The monorepo has two deploy paths and **both** must run after any change: `pipx reinstall council` and `bash loom/setup-runtime.sh`.

## Scope & Decisions

- **D-1 — One uniquely-named var per project, all in `~/.env`.** Projects do not reliably load a local `.env`, and where they do (`swimtrack-website`, via `process.loadEnvFile`) the *ambient* variable takes precedence — so a project-local `.env` holding a generic name is silently overridden by `~/.env`. A unique name in `~/.env` is the only mechanism that works uniformly.
- **D-2 — Naming convention:** `VENICE_<PROJECT>_KEY`, uppercased, hyphens→underscores.
- **D-3 — `romance`, `swimtrack-coach`, and `venice-ai-skill` need no code change.** `romance` already reads `VENICE_KEY` exclusively; the other two read isolated project `.env` files that no other consumer shares.
- **D-4 — The reporter's `delta$` column is removed, not fixed.** It subtracts Venice's *billed USD* from the ledger's *notional price-table estimate*. Under `usd: 0` caps the billed figure is always `0.0000` while the estimate is not, so the delta is guaranteed noise. Replaced with a `diem` column, which is what actually gets consumed.
- **D-5 — The proposed `off_box` config is dropped (YAGNI).** It existed to stop off-box keys rendering as `uncovered`. All five off-box keys are being revoked, so after Task 6 every remaining key is on-box and the list would always be empty.
- **D-6 — Sequencing is load-bearing:** code (with fallbacks) → deploy → Venice-side ops → paste keys → verify. Any other order either breaks callers or silently keeps billing to `DEFAULT`.
- **D-7 — Cron does not see `~/.env`, so the drain must pass the keys through explicitly.** Debian cron runs the nightly `diem drain` under `/bin/sh`; `~/.zshenv` is zsh-only and never fires, so the drain process has **no** `VENICE_*` in its environment. `_drain_env` therefore reads `~/.env` itself and forwards every `VENICE_*` entry to the subprocesses it spawns. Without that, `loom backfill` — loom's *only* Venice path, and one that runs exclusively from this cron — falls back to the shared key and bills to `DEFAULT` forever, and Task 6 Step 8's acceptance check fails for loom. The same change stops the drain clobbering `VENICE_KEY`, which is romance's var under this map.
- **D-8 — Blast radius, accepted knowingly.** After Step 6, `~/.env` exports **five** project Venice keys into every zsh process on the box, including every Claude Code session and the MCP servers it spawns. `council/venice.py::_scrub` redacts only the key that client is holding, so the other four are not covered by that defense-in-depth. This is inherent to D-1 (unique names in `~/.env`), not to any one change, and it is the largest-blast-radius decision in the plan.

## Key & Variable Map (target end state)

| ledger tag | Venice key (after rename/mint) | env var | fallback | code change? |
|---|---|---|---|---|
| `romance` | `romance` ← rename `romance-empire` | `VENICE_KEY` | — | no |
| `council` | `council` ← rename `AI-Council` | `VENICE_COUNCIL_KEY` | `VENICE_API_KEY` | Task 1 |
| `loom` | `loom` ← **mint** | `VENICE_LOOM_KEY` | `VENICE_API_KEY` | Task 2 |
| `swimtrack` | `swimtrack` ← rename `Swimtrack` | `VENICE_SWIMTRACK_KEY` | `VENICE_INFERENCE_KEY` | Task 4 |
| `swimtrack-website` | `swimtrack-website` ← rename `SwimTrack-Images` | `VENICE_SWIMTRACK_WEBSITE_KEY` | `VENICE_API_KEY` / `VENICE_INFERENCE_KEY` | Task 5 |
| `swimtrack-coach` | `swimtrack-coach` ← rename `swimtrack_coach` | `VENICE_API_KEY` in `.env.local` | — | no |
| `venice-ai-skill` | `venice-ai-skill` ← **mint** | `VENICE_API_KEY` in skill `.env` | — | no |
| *(fallback)* | `DEFAULT` | `VENICE_API_KEY` | — | no |
| *(admin)* | `ADMIN` | `VENICE_ADMIN_KEY` | — | no |
| *(CI)* | `CI` | GitHub Actions secret | — | no |

**Revoke:** `MacWhisper` (uncapped ADMIN, idle since 2025-10-16), `n8n`, `OpenClaw`, `Claude Code`, `GameBuilding`.

## File Structure

- `council/config.py` — `get_api_key()` gains the council-first chain. (Task 1)
- `loom/backends.py` — `get_backend()` gains the loom-first chain. (Task 2)
- `diem/cli.py` — `_cmd_venice_usage()` surfaces DIEM, drops `delta`. (Task 3)
- `swimtrack: editorial/src/editorial/config.py` — `get_api_key()` chain. (Task 4)
- `swimtrack-website: tools/image-engine/engine/venice.py` + `tools/i18n/translate.mjs` — chains. (Task 5)
- No new files. Every change is a lookup-order change inside one existing function.

---

### Task 1: council reads its own key

**Files:**
- Modify: `council/config.py:21-28` (`get_api_key`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `council.config.get_api_key() -> str` — unchanged signature, new lookup order. Task 3 does not depend on it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from council.config import get_api_key


def test_get_api_key_prefers_the_council_key(monkeypatch):
    monkeypatch.setenv("VENICE_COUNCIL_KEY", "council-key")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_api_key() == "council-key"


def test_get_api_key_falls_back_to_the_shared_key(monkeypatch):
    monkeypatch.delenv("VENICE_COUNCIL_KEY", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_api_key() == "default-key"


def test_get_api_key_treats_blank_as_unset(monkeypatch):
    # A set-but-empty var must fall through, not be returned as a valid key.
    monkeypatch.setenv("VENICE_COUNCIL_KEY", "")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_api_key() == "default-key"


def test_get_api_key_exits_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("VENICE_COUNCIL_KEY", raising=False)
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        get_api_key()
```

Ensure `import pytest` is present at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py -k get_api_key -v`
Expected: `test_get_api_key_prefers_the_council_key` FAILS (returns `"default-key"`); the other three PASS against the current implementation.

- [ ] **Step 3: Write the implementation**

Replace `get_api_key` in `council/config.py`:

```python
# One Venice key per project, so per-key billing attributes spend correctly.
# The generic VENICE_API_KEY stays as a fallback: it is what `~/.env` puts in
# every shell, so this code is safe to deploy before the council key exists.
_KEY_VARS = ("VENICE_COUNCIL_KEY", "VENICE_API_KEY")


def get_api_key() -> str:
    for name in _KEY_VARS:
        key = os.environ.get(name)
        if key:
            return key
    print("error: no Venice key set. Add VENICE_COUNCIL_KEY (preferred) or "
          "VENICE_API_KEY to your environment or .env (see .env.example).",
          file=sys.stderr)
    raise SystemExit(2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -k get_api_key -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite for regressions**

Run: `pytest -q`
Expected: all pass (baseline: 426 passed, 4 skipped).

- [ ] **Step 6: Commit**

```bash
git add council/config.py tests/test_config.py
git commit -m "feat(venice-keys): council reads VENICE_COUNCIL_KEY, falls back to shared"
```

---

### Task 2: loom reads its own key

**Files:**
- Modify: `loom/backends.py:38-44` (`get_backend`)
- Test: `tests/loom/test_backends.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `loom.backends.get_backend(name: str, api_key: Optional[str] = None) -> Backend` — unchanged signature. An explicit `api_key` argument still wins over both env vars.

- [ ] **Step 1: Write the failing tests**

Append to `tests/loom/test_backends.py` (create it if absent, with `from loom.backends import get_backend`):

```python
def test_venice_backend_prefers_the_loom_key(monkeypatch):
    monkeypatch.setenv("VENICE_LOOM_KEY", "loom-key")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_backend("venice")._client.api_key == "loom-key"


def test_venice_backend_falls_back_to_the_shared_key(monkeypatch):
    monkeypatch.delenv("VENICE_LOOM_KEY", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_backend("venice")._client.api_key == "default-key"


def test_explicit_api_key_wins_over_both(monkeypatch):
    monkeypatch.setenv("VENICE_LOOM_KEY", "loom-key")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_backend("venice", api_key="explicit")._client.api_key == "explicit"


def test_blank_loom_key_falls_through(monkeypatch):
    monkeypatch.setenv("VENICE_LOOM_KEY", "")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_backend("venice")._client.api_key == "default-key"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/loom/test_backends.py -v`
Expected: `test_venice_backend_prefers_the_loom_key` FAILS (returns `"default-key"`).

- [ ] **Step 3: Write the implementation**

Replace the `venice` branch of `get_backend` in `loom/backends.py`:

```python
def get_backend(name: str, api_key: Optional[str] = None) -> Backend:
    if name == "claude":
        return ClaudeBackend()
    if name == "venice":
        import os
        # One Venice key per project; VENICE_API_KEY remains the fallback so
        # this is safe to deploy before the loom key is minted.
        key = (api_key
               or os.environ.get("VENICE_LOOM_KEY")
               or os.environ.get("VENICE_API_KEY", ""))
        return VeniceBackend(key)
    raise ValueError(f"unknown backend: {name}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/loom/test_backends.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add loom/backends.py tests/loom/test_backends.py
git commit -m "feat(venice-keys): loom reads VENICE_LOOM_KEY, falls back to shared"
```

---

### Task 3: reporter shows DIEM instead of a meaningless delta

**Files:**
- Modify: `diem/cli.py:114-148` (`_cmd_venice_usage`)
- Test: `tests/diem/test_cli.py`

**Interfaces:**
- Consumes: `UsageClient.per_key_usage() -> list[dict]` with keys `key_id`, `key_name`, `usd`, `diem` (already built in Phase B — `diem/usage.py:45-46`).
- Produces: JSON rows with keys `project`, `est_usd`, `venice_usd`, `venice_diem`, `note`. **The `delta` key is removed** — any consumer reading it must be updated (there are none outside the tests).

**Why:** the ledger's USD is a price-table estimate; Venice bills DIEM and, under `usd: 0` caps, reports `0.0000` USD forever. Subtracting the two produced a guaranteed-wrong number. Verified live: the `$27.45` romance run cost `$0.00 + 60.44 DIEM`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/diem/test_cli.py` (the existing diem CLI test module):

```python
def test_rows_report_diem_and_have_no_delta(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "t.db"))
    venice_usage.append(project="council", task_type="ask", model="m", usd=1.25)

    class FakeClient:
        def __init__(self, *a, **k): pass
        def per_key_usage(self):
            return [{"key_id": "1", "key_name": "council", "usd": 0.0, "diem": 12.5}]

    monkeypatch.setattr(diem.cli, "UsageClient", FakeClient)
    monkeypatch.setattr(diem.cli, "load_venice_admin_key", lambda: "admin")

    diem.cli._cmd_venice_usage(None, datetime(2026, 7, 20), as_json=True)
    payload = json.loads(capsys.readouterr().out)
    row = next(r for r in payload["rows"] if r["project"] == "council")

    assert row["est_usd"] == 1.25
    assert row["venice_usd"] == 0.0
    assert row["venice_diem"] == 12.5
    assert "delta" not in row


def test_table_header_labels_the_estimate_and_shows_diem(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "t.db"))
    venice_usage.append(project="council", task_type="ask", model="m", usd=1.25)

    class FakeClient:
        def __init__(self, *a, **k): pass
        def per_key_usage(self):
            return [{"key_id": "1", "key_name": "council", "usd": 0.0, "diem": 12.5}]

    monkeypatch.setattr(diem.cli, "UsageClient", FakeClient)
    monkeypatch.setattr(diem.cli, "load_venice_admin_key", lambda: "admin")

    diem.cli._cmd_venice_usage(None, datetime(2026, 7, 20))
    out = capsys.readouterr().out
    assert "est$" in out and "diem" in out
    assert "delta" not in out
    # The estimate must be labelled as notional so it is never read as billed spend.
    assert "estimate" in out.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/diem/test_cli.py -k "diem_and_have_no_delta or header_labels" -v`
Expected: FAIL — `KeyError: 'est_usd'` / `assert "delta" not in row`.

- [ ] **Step 3: Write the implementation**

Replace `_cmd_venice_usage` in `diem/cli.py`:

```python
def _cmd_venice_usage(cfg, now, *, days=7, as_json=False) -> int:
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    ledger = {r["project"]: r["usd"]
              for r in venice_usage.query_rollup(since=since, group_by=("project",))}
    venice_usd: dict[str, float] = {}
    venice_diem: dict[str, float] = {}
    warn = None
    try:
        for k in UsageClient(load_venice_admin_key()).per_key_usage():
            name = k["key_name"]
            proj = name[len("proj-"):] if name.startswith("proj-") else name
            venice_usd[proj] = venice_usd.get(proj, 0.0) + k["usd"]
            venice_diem[proj] = venice_diem.get(proj, 0.0) + k["diem"]
    except (UsageUnavailable, SystemExit) as e:
        warn = str(e) or "venice usage unavailable"
    projects = sorted(set(ledger) | set(venice_usd))
    rows = []
    for p in projects:
        lu, vu, vd = ledger.get(p), venice_usd.get(p), venice_diem.get(p)
        note = "" if (lu is not None and vu is not None) else \
               ("uncovered" if lu is None else "no key")
        rows.append({"project": p,
                     "est_usd": round(lu or 0.0, 4),
                     "venice_usd": None if vu is None else round(vu, 4),
                     "venice_diem": None if vd is None else round(vd, 4),
                     "note": note})
    if as_json:
        print(json.dumps({"days": days, "warning": warn, "rows": rows}, indent=1))
        return 0
    if warn:
        print(f"warning: Venice usage unavailable ({warn}) — showing ledger only")
    print(f"venice-usage reconcile (last {days}d)")
    # est$ is the ledger's price-table estimate, NOT billed spend. Inference keys are
    # capped usd:0 and run on the DIEM allowance, so venice$ is normally 0.0000 and
    # `diem` is the figure that reflects real consumption.
    print("est$ = ledger estimate (notional); venice$ = billed USD; diem = allowance used")
    print(f"{'project':16} {'est$':>9} {'venice$':>9} {'diem':>9}  note")
    for r in rows:
        vu = "-" if r["venice_usd"] is None else f"{r['venice_usd']:.4f}"
        vd = "-" if r["venice_diem"] is None else f"{r['venice_diem']:.4f}"
        print(f"{r['project']:16} {r['est_usd']:9.4f} {vu:>9} {vd:>9}  {r['note']}")
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/diem/ -v`
Expected: all pass, including the pre-existing degrade-path and coverage-note tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add diem/cli.py tests/diem/test_cli.py
git commit -m "fix(venice-usage): reconcile against DIEM; drop notional-vs-billed delta"
```

---

### Task 4: swimtrack editorial reads its own key

**Repo:** `~/projects/swimtrack` — branch from `main` as `claude/venice-key-partition`.

**Files:**
- Modify: `editorial/src/editorial/config.py:41-45` (`get_api_key`)
- Test: `editorial/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `editorial.config.get_api_key() -> str` — unchanged signature. Raises `RuntimeError` (not `SystemExit`) when unset, matching the existing contract.

**Note:** this code path currently raises on every invocation — `~/.env` does not define `VENICE_INFERENCE_KEY` and the repo has no `.env`, only `.env.example`. Task 6 is what actually makes it work.

- [ ] **Step 1: Write the failing tests**

Append to `editorial/tests/test_config.py`:

```python
import pytest
from editorial.config import get_api_key


def test_prefers_the_swimtrack_key(monkeypatch):
    monkeypatch.setenv("VENICE_SWIMTRACK_KEY", "swimtrack-key")
    monkeypatch.setenv("VENICE_INFERENCE_KEY", "generic-key")
    assert get_api_key() == "swimtrack-key"


def test_falls_back_to_the_generic_key(monkeypatch):
    monkeypatch.delenv("VENICE_SWIMTRACK_KEY", raising=False)
    monkeypatch.setenv("VENICE_INFERENCE_KEY", "generic-key")
    assert get_api_key() == "generic-key"


def test_blank_falls_through(monkeypatch):
    monkeypatch.setenv("VENICE_SWIMTRACK_KEY", "")
    monkeypatch.setenv("VENICE_INFERENCE_KEY", "generic-key")
    assert get_api_key() == "generic-key"


def test_raises_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("VENICE_SWIMTRACK_KEY", raising=False)
    monkeypatch.delenv("VENICE_INFERENCE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_api_key()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/swimtrack/editorial && pytest tests/test_config.py -v`
Expected: `test_prefers_the_swimtrack_key` FAILS.

- [ ] **Step 3: Write the implementation**

Replace `get_api_key` in `editorial/src/editorial/config.py`:

```python
# One Venice key per project. VENICE_INFERENCE_KEY stays as a fallback so this
# is safe to deploy before the swimtrack key is wired up.
_KEY_VARS = ("VENICE_SWIMTRACK_KEY", "VENICE_INFERENCE_KEY")


def get_api_key() -> str:
    for name in _KEY_VARS:
        key = os.environ.get(name)
        if key:
            return key
    raise RuntimeError(
        "no Venice key set — add VENICE_SWIMTRACK_KEY (preferred) or "
        "VENICE_INFERENCE_KEY (copy editorial/.env.example to .env)")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/swimtrack/editorial && pytest -q`
Expected: all pass (baseline: 64 passed).

- [ ] **Step 5: Commit**

```bash
git add editorial/src/editorial/config.py editorial/tests/test_config.py
git commit -m "feat(venice-keys): editorial reads VENICE_SWIMTRACK_KEY, falls back to generic"
```

---

### Task 5: swimtrack-website reads its own key (both tools)

**Repo:** `~/projects/swimtrack-website` — branch from `main` as `claude/venice-key-partition`.

**Files:**
- Modify: `tools/image-engine/engine/venice.py:21-37` (`load_api_key`)
- Modify: `tools/i18n/translate.mjs:105`
- Test: `tools/image-engine/tests/test_venice_key.py`, `tools/i18n/venice.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_api_key() -> str` (Python, unchanged signature); `translate.mjs` passes the resolved key into the existing `createVeniceClient({apiKey, model})`.

**Why both tools share one key:** they are one project (`swimtrack-website`) and one ledger tag. Two tools, one key, one Venice-side total.

- [ ] **Step 1: Write the failing Python test**

Create `tools/image-engine/tests/test_venice_key.py`:

```python
import os
import pytest
from engine.venice import load_api_key


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for n in ("VENICE_SWIMTRACK_WEBSITE_KEY", "VENICE_INFERENCE_KEY", "VENICE_API_KEY"):
        monkeypatch.delenv(n, raising=False)


def test_prefers_the_website_key(monkeypatch):
    monkeypatch.setenv("VENICE_SWIMTRACK_WEBSITE_KEY", "website-key")
    monkeypatch.setenv("VENICE_INFERENCE_KEY", "generic-key")
    assert load_api_key() == "website-key"


def test_falls_back_through_the_generic_names(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert load_api_key() == "default-key"


def test_blank_website_key_falls_through(monkeypatch):
    monkeypatch.setenv("VENICE_SWIMTRACK_WEBSITE_KEY", "")
    monkeypatch.setenv("VENICE_INFERENCE_KEY", "generic-key")
    assert load_api_key() == "generic-key"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/projects/swimtrack-website/tools/image-engine && python -m pytest tests/test_venice_key.py -v`
Expected: `test_prefers_the_website_key` FAILS (returns `"generic-key"`).

- [ ] **Step 3: Implement the Python change**

Replace `load_api_key` in `tools/image-engine/engine/venice.py`:

```python
# One Venice key per project: the website's own key first, then the legacy
# generic names, then the tool's own .env file.
_KEY_VARS = ("VENICE_SWIMTRACK_WEBSITE_KEY", "VENICE_INFERENCE_KEY", "VENICE_API_KEY")


def load_api_key() -> str:
    """Project key from the env, else a generic name, else the tool's .env file."""
    for name in _KEY_VARS:
        key = os.environ.get(name)
        if key:
            return key
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in _KEY_VARS:
                    return v.strip().strip("\"'")
    raise SystemExit(
        "ERROR: no Venice key found in the environment or tools/image-engine/.env "
        f"(tried: {', '.join(_KEY_VARS)})")
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd ~/projects/swimtrack-website/tools/image-engine && python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Write the failing Node test**

Append to `tools/i18n/venice.test.mjs`:

```javascript
import { resolveApiKey } from './translate.mjs';

describe('resolveApiKey', () => {
  it('prefers the website key', () => {
    expect(resolveApiKey({
      VENICE_SWIMTRACK_WEBSITE_KEY: 'website-key',
      VENICE_API_KEY: 'default-key',
    })).toBe('website-key');
  });

  it('falls back to the shared key', () => {
    expect(resolveApiKey({ VENICE_API_KEY: 'default-key' })).toBe('default-key');
  });

  it('treats a blank website key as unset', () => {
    expect(resolveApiKey({
      VENICE_SWIMTRACK_WEBSITE_KEY: '',
      VENICE_API_KEY: 'default-key',
    })).toBe('default-key');
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd ~/projects/swimtrack-website && npx vitest run tools/i18n/venice.test.mjs`
Expected: FAIL — `resolveApiKey is not a function`.

- [ ] **Step 7: Implement the Node change**

In `tools/i18n/translate.mjs`, add the exported helper near the CLI section:

```javascript
// One Venice key per project. The website's own key first; VENICE_API_KEY stays
// as a fallback. Note that `process.loadEnvFile` lets REAL environment variables
// win over a local .env, so a project .env holding a generic name is silently
// overridden by ~/.env — a uniquely-named variable is the only reliable route.
export function resolveApiKey(env = process.env) {
  return env.VENICE_SWIMTRACK_WEBSITE_KEY || env.VENICE_API_KEY;
}
```

and change line 105 to use it:

```javascript
  const client = mode === 'run'
    ? createVeniceClient({ apiKey: resolveApiKey(), model })
    : { completeJSON: async () => { throw new Error('no client in this mode'); } };
```

- [ ] **Step 8: Run the Node tests to verify they pass**

Run: `cd ~/projects/swimtrack-website && npx vitest run && npx eslint tools/`
Expected: all pass (baseline: 232 passed), eslint clean.

- [ ] **Step 9: Commit**

```bash
git add tools/image-engine/engine/venice.py tools/image-engine/tests/test_venice_key.py \
        tools/i18n/translate.mjs tools/i18n/venice.test.mjs
git commit -m "feat(venice-keys): swimtrack-website tools read VENICE_SWIMTRACK_WEBSITE_KEY"
```

---

### Task 6: Ops runbook — Venice-side changes and key wiring

**No TDD gate — this is ops.** Every step that handles a key value uses a silent prompt. Do not proceed until Tasks 1–5 are merged **and deployed**, because the fallbacks are what keep callers alive between the code change and the key paste.

- [ ] **Step 1: Merge and deploy all code changes**

**Ordering is load-bearing.** `pipx reinstall council` installs from the shared checkout's
**working tree**, not from a git ref (`pipx_metadata.json` records
`package_or_url: /home/dev/projects/build-ai-automation-workflow` with `--force-reinstall`).
Sessions routinely flip that tree between branches — the very reason `~/loom-runtime` exists —
so the checkout must be sitting on merged `main` *before* the reinstall, or an unrelated
branch gets baked into the global commands.

```bash
# merge each repo first (merge --no-ff, push, delete the branch), then:
git -C ~/projects/build-ai-automation-workflow checkout main
git -C ~/projects/build-ai-automation-workflow pull          # tree now on merged main
cd ~/projects/build-ai-automation-workflow
pipx reinstall council          # refreshes council / diem / venice-usage on PATH
bash loom/setup-runtime.sh      # refreshes ~/loom-runtime (the 02:00 absorb cron's clone)
```

Verify both: `venice-usage report --group-by project` runs, and
`cd / && PYTHONPATH=/home/dev/loom-runtime /home/dev/loom-runtime/.venv/bin/python -c "import loom.venice, inspect; print('venice_usage' in inspect.getsource(loom.venice))"` prints `True`.

**Note which loom actually changed.** `loom` is not in the pipx venv (that ships `council`,
`diem`, `venice_usage`). Loom reaches production two ways: `~/loom-runtime`, refreshed by
`setup-runtime.sh` — but that path runs only `absorb`, which is Claude-backed and never
touches the changed Venice code; and the shared repo's editable `.venv`, which is what the
**drain** invokes (`diem/config.py` → `~/projects/build-ai-automation-workflow/.venv/bin/python -m loom.cli backfill`).
That second path has no deploy command — it goes live the moment the shared tree sits on
merged `main`, which the ordering above already guarantees.

- [ ] **Step 1b: Audit the GitHub Actions secrets against the new key map**

CI does not read `~/.env`, so nothing in Step 6 reaches it. `swimtrack`'s
`.github/workflows/editorial-scan.yml` runs daily at 06:00 and is explicitly commented
"recurring Venice spend", passing `VENICE_INFERENCE_KEY: ${{ secrets.VENICE_INFERENCE_KEY }}`.
After this rollout CI has no `VENICE_SWIMTRACK_KEY`, so it falls through to that secret while
local runs use the project key — splitting swimtrack's spend across two Venice keys unless the
secret already holds the renamed `swimtrack` key's value. The Step 2 rename preserves key
values, so this may already be correct; confirm rather than assume.

```bash
gh secret list -R rexmcintosh/swimtrack            # expect VENICE_INFERENCE_KEY
gh secret list -R rexmcintosh/build-ai-automation-workflow   # VENICE_API_KEY (review CI)
```

Check the same for whichever repos back `venice-review.yml`. Re-paste any secret whose value
no longer matches the key its project should now bill to.

- [ ] **Step 2: Rename five keys on the Venice site** (user, https://venice.ai/settings/api)

| from | to |
|---|---|
| `romance-empire` | `romance` |
| `AI-Council` | `council` |
| `Swimtrack` | `swimtrack` |
| `swimtrack_coach` | `swimtrack-coach` |
| `SwimTrack-Images` | `swimtrack-website` |

Renaming does not change a key's value, so nothing breaks at this step.

- [ ] **Step 3: Mint two new keys** (user) — `loom` and `venice-ai-skill`, both INFERENCE.

- [ ] **Step 4: Set `usd: 0` on every INFERENCE key** (user) — all seven project keys plus `DEFAULT` and `CI`. This is the decided policy: DIEM-only, no dollar spend possible. **`romance` currently has a `usd: 10` cap and has already spent $7.55 of it** — setting it to 0 is what stops that.

- [ ] **Step 5: Revoke five keys** (user) — `MacWhisper` (uncapped ADMIN, idle since 2025-10-16), `n8n`, `OpenClaw`, `Claude Code`, `GameBuilding`.

- [ ] **Step 6: Paste key values into `~/.env`** — one silent prompt per key. The value never appears on screen, in `argv`, or in history (`printf` is a shell builtin; the command line contains `$K`, not the value).

**zsh syntax** (this box's shell): the prompt attaches to the variable name as
`read -rs "K?prompt"` — the bash form `read -rsp "prompt" K` does *not* work here.
`-s` suppresses echo; `printf` is a builtin, so no value ever reaches a process
argument list. Back `~/.env` up first — the rewrite is in-place.

```bash
umask 077
cp ~/.env ~/.env.bak-$(date +%Y%m%d)      # in-place rewrite below; keep a way back
for VAR in VENICE_KEY VENICE_COUNCIL_KEY VENICE_LOOM_KEY \
           VENICE_SWIMTRACK_KEY VENICE_SWIMTRACK_WEBSITE_KEY; do
  read -rs "K?paste value for $VAR: "; echo
  # drop any existing line for this var, then append the new one.
  # `|| true`: grep -v exits 1 when it emits no lines, which is not an error here.
  { grep -v "^${VAR}=" ~/.env || true; } > ~/.env.tmp
  mv ~/.env.tmp ~/.env
  printf '%s=%s\n' "$VAR" "$K" >> ~/.env
  unset K
done
chmod 600 ~/.env
```

Sanity-check the result without revealing anything: `grep -c . ~/.env` and
`grep -oE '^[A-Z_]+' ~/.env` should list the expected variable names and nothing else.

Then the two isolated files, same pattern:

```bash
read -rs "K?paste value for the venice-ai-skill key: "; echo
printf 'VENICE_API_KEY=%s\n' "$K" > ~/.claude/skills/venice-ai/.env; unset K
chmod 600 ~/.claude/skills/venice-ai/.env

read -rs "K?paste value for the swimtrack-coach key: "; echo
{ grep -v '^VENICE_API_KEY=' ~/projects/swimtrack-coach/.env.local || true; } > /tmp/e
mv /tmp/e ~/projects/swimtrack-coach/.env.local
printf 'VENICE_API_KEY=%s\n' "$K" >> ~/projects/swimtrack-coach/.env.local; unset K
chmod 600 ~/projects/swimtrack-coach/.env.local
```

`swimtrack-coach` already holds the right key — re-paste only if Step 2's rename was accompanied by a re-issue.

- [ ] **Step 7: Verify wiring without printing any secret**

Re-run the `last6Chars` mapping (the script used on 2026-07-20) and confirm each file resolves to the expected key **by name**. Expected: `~/.env`'s five project vars map to `romance`, `council`, `loom`, `swimtrack`, `swimtrack-website`; the skill file to `venice-ai-skill`; `.env.local` to `swimtrack-coach`.

- [ ] **Step 8: Confirm attribution after Venice's reporting lag**

Venice's usage figures lag by up to a day, so this check belongs a few days later, not immediately.

Run: `diem venice-usage --days 7`
Expected: each project shows non-zero `diem` against its **own** key, `venice$` is `0.0000` everywhere (the `usd: 0` caps holding), and no row is flagged `no key` or `uncovered`.

- [ ] **Step 9: Update the source-of-truth docs**

Record the final key names, caps, revocations, and the new variable map in
`/home/dev/docs/SECRETS-AND-DEPLOY-MAP.md` and append a dated section to
`/home/dev/docs/secrets-map-notes-2026-07-18.md`.

---

## Sequencing

1. **Tasks 1–3** (monorepo) can run in parallel — they touch three different files.
2. **Tasks 4 and 5** are independent repos; run them in parallel with 1–3.
3. **Task 6 is strictly last** and requires the user for Steps 2–6.
4. Step 8 of Task 6 is deferred by a few days (Venice reporting lag).

## Out of Scope

- **Telegram token consolidation** (`~/.config/diem/config.toml` and `~/.claude/channels/telegram/.env`) — unrelated to Venice; a separate small ops item.
- **Hermes provider-side revokes** (Notion token, Google OAuth) — user action, tracked in the notes.
- **`off_box` config** — obsoleted by the Task 6 Step 5 revocations (see D-5).

## Self-Review

**Spec coverage:** All seven canonical tags are accounted for — `council` (T1), `loom` (T2), `swimtrack` (T4), `swimtrack-website` (T5), and `romance`/`swimtrack-coach`/`venice-ai-skill` explicitly needing no code change (D-3), each wired in T6. The reporter defect is T3. Caps, renames, mints, and revocations are T6 Steps 2–5.

**Placeholder scan:** No TBDs. Every code step carries complete code; every test step carries complete test bodies; every command has an expected result.

**Type consistency:** `get_api_key() -> str` keeps its signature in both Python repos, raising `SystemExit` in council (matching its current contract) and `RuntimeError` in editorial (matching *its* current contract) — deliberately different, as noted in each task's Interfaces block. `load_api_key() -> str` and `get_backend(name, api_key=None)` are unchanged. `resolveApiKey(env)` is new and exported from `translate.mjs`, consumed only there and in its test. The `_KEY_VARS` constant name is reused per-module with different contents — module-scoped, no collision.

**One risk accepted:** T3 removes the `delta` JSON key. Grep confirms no consumer outside the tests reads it.
