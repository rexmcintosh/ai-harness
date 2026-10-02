# Loom Usage-Limit Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make nightly `loom absorb` detect the Claude subscription usage limit, stop early (leaving targets pending for the next run), report the pause clearly, and run headless calls with no plugins so no Telegram helper is spawned or leaked.

**Architecture:** A new `UsageLimitError` is raised by the `claude -p` wrapper when Claude's output shows a usage/session limit. The orchestrator catches it in both the distill and weave stages, sets `limit_hit`, and stops without marking work failed. The wrapper also passes a committed `--settings` file that disables plugins. The summary formatter renders a "paused" headline.

**Tech Stack:** Python 3, pytest. Package `loom/` in `/home/dev/projects/build-ai-automation-workflow`.

## Global Constraints

- Edit only the shared repo `loom/` — never `/home/dev/loom-runtime` (it is `git reset --hard` each run).
- Run tests from the repo root with `.venv/bin/pytest`.
- `run.py` and `backends.py` already do `from . import llm`; reference the new error as `llm.UsageLimitError`.
- The no-plugins settings file must live inside `loom/` so it survives the runtime clone's `git reset`.
- Follow existing test patterns: distill-only tests monkeypatch `run_mod.llm.run`; weave tests monkeypatch `run_mod.get_backend` to return a fake backend with `.complete(role, system, user, json_mode=False)`.
- The verified plugin-disable content is `{"enabledPlugins": {"telegram@claude-plugins-official": false}}` (proven 2026-07-12 to suppress the helper while `claude -p` still returns normally).

---

### Task 1: `llm.py` — surface the error, classify the limit, drop plugins

**Files:**
- Modify: `loom/llm.py`
- Create: `loom/headless-settings.json`
- Test: `tests/loom/test_llm.py` (create)

**Interfaces:**
- Produces: `llm.UsageLimitError(LLMError)`; `llm.build_argv(model, ...)` now includes `--settings <abs path to loom/headless-settings.json>`; `llm.run(...)` raises `UsageLimitError` on a usage-limit message, else `LLMError` (message now built from stdout+stderr).

- [ ] **Step 1: Write the failing tests**

Create `tests/loom/test_llm.py`:
```python
# tests/loom/test_llm.py
import pytest
from loom import llm


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_build_argv_disables_plugins_via_settings():
    argv = llm.build_argv("sonnet")
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1].endswith("headless-settings.json")


def test_run_raises_usage_limit_error_on_session_limit(monkeypatch):
    def fake_run(argv, **kwargs):
        return _Proc(1, stdout="You've hit your session limit · resets 5:10am (Europe/Lisbon)")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    with pytest.raises(llm.UsageLimitError):
        llm.run("hi", model="sonnet")


def test_run_raises_plain_llmerror_with_stdout_on_generic_failure(monkeypatch):
    def fake_run(argv, **kwargs):
        return _Proc(1, stdout="some diagnostic on stdout", stderr="")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    with pytest.raises(llm.LLMError) as ei:
        llm.run("hi", model="sonnet")
    assert not isinstance(ei.value, llm.UsageLimitError)
    assert "some diagnostic on stdout" in str(ei.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/loom/test_llm.py -v`
Expected: FAIL (`AttributeError: module 'loom.llm' has no attribute 'UsageLimitError'`, and `--settings` not in argv).

- [ ] **Step 3: Implement the changes in `loom/llm.py`**

Add `import re` and `from pathlib import Path` to the imports. Add after `class LLMError`:
```python
class UsageLimitError(LLMError):
    """Claude rejected the call because the subscription usage/session limit is
    exhausted. The notice appears on stdout, e.g. 'You've hit your session limit'."""


# Printed on stdout with a non-zero exit. A miss just degrades to a generic
# LLMError (retried next run), so keep the set specific to avoid false positives.
_USAGE_LIMIT_RE = re.compile(
    r"hit your session limit|usage limit|limit reached|rate limit", re.IGNORECASE
)

_SETTINGS_PATH = str(Path(__file__).parent / "headless-settings.json")
```
Change `build_argv`'s first line to include the settings flag:
```python
    argv = [_claude_bin(), "-p", "-", "--model", model, "--output-format", "text",
            "--settings", _SETTINGS_PATH]
```
Replace the failure branch in `run()`:
```python
    if proc.returncode != 0:
        detail = ((proc.stdout or "").strip() + "\n" + (proc.stderr or "").strip()).strip()
        if _USAGE_LIMIT_RE.search(detail):
            raise UsageLimitError(f"claude usage limit: {detail[:500]}")
        raise LLMError(f"claude exited {proc.returncode}: {detail[:500]}")
    return proc.stdout.strip()
```

- [ ] **Step 4: Create `loom/headless-settings.json`**

```json
{
  "enabledPlugins": {
    "telegram@claude-plugins-official": false
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/loom/test_llm.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add loom/llm.py loom/headless-settings.json tests/loom/test_llm.py
git commit -m "feat(loom): classify usage-limit errors + disable plugins in headless claude"
```

---

### Task 2: `run.py` — abort on the limit, don't hammer

**Files:**
- Modify: `loom/run.py` (`absorb`, `_weave_all`)
- Test: `tests/loom/test_run.py` (append)

**Interfaces:**
- Consumes: `llm.UsageLimitError` (Task 1).
- Produces: `absorb(...)` returns `summary` with a `"limit_hit"` bool; on a usage limit it stops without incrementing `failed` or advancing state, and skips the weave stage.

- [ ] **Step 1: Write the failing tests**

Append to `tests/loom/test_run.py`:
```python
def test_distill_aborts_on_usage_limit(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    for name in ("sessA", "sessB"):
        t = projects / "p1" / f"{name}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text('{"type":"user","message":{"content":"hi"}}\n')
    cfg = run_mod.Config(projects_dir=projects, loom_dir=tmp_path / "loom",
                         state_path=tmp_path / "loom" / "state.json")
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    calls = {"n": 0}
    def limited(prompt, model, **k):
        calls["n"] += 1
        raise run_mod.llm.UsageLimitError("claude usage limit")
    monkeypatch.setattr(run_mod.llm, "run", limited)
    summary = run_mod.absorb(cfg, shadow=True)
    assert calls["n"] == 1                        # broke after the first limit; sessB untouched
    assert summary["limit_hit"] is True
    assert summary["distilled"] == 0 and summary["failed"] == 0
    assert LoomState(cfg.state_path).state_of("sessA") == "pending"   # not advanced


def test_usage_limit_in_distill_skips_weave(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    roles_seen = []
    class B:
        def complete(self, role, system, user, json_mode=False):
            roles_seen.append(role)
            raise run_mod.llm.UsageLimitError("limit")
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude")
    assert summary["limit_hit"] is True
    assert summary["committed"] == 0
    assert roles_seen == ["distill"]              # weave/route never attempted


def test_usage_limit_in_weave_is_caught(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    class B:
        def complete(self, role, system, user, json_mode=False):
            if role == "distill":
                return "- type: fact\n  subject: x\n  learning: y\n  route: wiki/people/x"
            raise run_mod.llm.UsageLimitError("limit during route/weave")
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude")   # must not raise
    assert summary["limit_hit"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/loom/test_run.py -k usage_limit -v`
Expected: FAIL (`KeyError: 'limit_hit'` and the weave test raising `UsageLimitError`).

- [ ] **Step 3: Implement the changes in `loom/run.py`**

In `absorb`, seed the flag in the `summary` dict:
```python
    summary = {"distilled": 0, "quarantined": 0, "failed": 0,
               "committed": 0, "deferred": 0, "rejected": 0, "deadline_hit": False,
               "limit_hit": False}
```
In the distill loop, insert a limit branch **before** the existing `except Exception`:
```python
            except llm.UsageLimitError:
                summary["limit_hit"] = True
                break
            except Exception:
                logging.exception("distill failed for %s", transcript)
                summary["failed"] += 1
                continue
```
After the `if distill:` block and before `if shadow:`, short-circuit:
```python
    if summary["limit_hit"]:
        return summary
    if shadow:
        return summary
```
Wrap the weave call so a limit there is caught, not fatal:
```python
    try:
        _weave_all(cfg, state, backend, max_targets, max_per_target, today, summary, _expired)
    except llm.UsageLimitError:
        summary["limit_hit"] = True
    return summary
```
In `_weave_all`, make the `weave_target` handler re-raise the limit (add **before** `except Exception`):
```python
        except llm.UsageLimitError:
            raise
        except Exception:
            logging.exception("weave_target failed for %s", target)
```

- [ ] **Step 4: Run the usage-limit tests to verify they pass**

Run: `.venv/bin/pytest tests/loom/test_run.py -k usage_limit -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full run-module suite for regressions**

Run: `.venv/bin/pytest tests/loom/test_run.py -v`
Expected: PASS (all existing tests still green).

- [ ] **Step 6: Commit**

```bash
git add loom/run.py tests/loom/test_run.py
git commit -m "feat(loom): abort absorb on usage limit, leave targets pending"
```

---

### Task 3: `summary.py` — report the pause honestly

**Files:**
- Modify: `loom/summary.py` (`build_summary`, `format_run_summary`)
- Test: `tests/loom/test_summary.py` (append)

**Interfaces:**
- Consumes: `summary["limit_hit"]` and `summary["distilled"]` (Task 2).
- Produces: `build_summary(..., limit_hit=False)`; `format_run_summary` renders a paused headline when `limit_hit`.

- [ ] **Step 1: Write the failing test**

Append to `tests/loom/test_summary.py`:
```python
def test_limit_hit_renders_paused_headline():
    d = {"distilled": 4, "failed": 0, "committed": 0, "deferred": 0,
         "limit_hit": True, "shadow_commits": 0, "oldest_age_days": 0}
    s = format_run_summary(d)
    assert "Paused" in s and "usage limit" in s.lower()
    assert "4" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/loom/test_summary.py::test_limit_hit_renders_paused_headline -v`
Expected: FAIL (no "Paused" in output).

- [ ] **Step 3: Implement the changes in `loom/summary.py`**

Change `build_summary`'s signature and prepend the headline:
```python
def build_summary(counts: Dict[str, int], shadow_commits: int, oldest_age_days: int,
                  rejected: List[Tuple[str, str]], proposed: List[str],
                  limit_hit: bool = False) -> str:
    parts = ["🧵 Loom run"]
    if limit_hit:
        parts.append(
            f"⏸️ Paused — Claude usage limit reached; distilled {counts.get('distilled', 0)} "
            f"before pausing, rest deferred to next run"
        )
    parts.append(" ".join(f"{k}={v}" for k, v in counts.items()))
```
In `format_run_summary`, pass the flag through:
```python
    return build_summary(counts=counts,
                         shadow_commits=int(d.get("shadow_commits", 0)),
                         oldest_age_days=int(d.get("oldest_age_days", 0)),
                         rejected=rejected,
                         proposed=d.get("proposed", []),
                         limit_hit=bool(d.get("limit_hit")))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/loom/test_summary.py -v`
Expected: PASS (all, including the new headline test and unchanged existing tests).

- [ ] **Step 5: Commit**

```bash
git add loom/summary.py tests/loom/test_summary.py
git commit -m "feat(loom): report usage-limit pause in the run summary"
```

---

### Task 4: Full-suite verification

- [ ] **Step 1: Run the whole loom suite**

Run: `.venv/bin/pytest tests/loom -v`
Expected: PASS (all green — new tests plus no regressions).

- [ ] **Step 2 (optional): widen plugin-disable to all plugins**

Spawn-probe whether disabling *all* installed plugins (not just Telegram) still lets `claude -p` return normally and suppresses every plugin's MCP server (same method as the 2026-07-12 A/B: start a headless call, poll for spawned MCP helpers). If confirmed, extend `loom/headless-settings.json` accordingly and re-run Task 1's tests. If not, leave Telegram-only (already fixes the leak).

## Self-review notes

- Spec coverage: usage-limit detection (Task 1), stdout capture (Task 1), plugin-disable (Task 1 + settings file), abort-without-failed + skip-weave (Task 2), weave-stage safety (Task 2), paused report (Task 3). All spec sections mapped.
- The `deferred`-count ambiguity from the spec was resolved to the known `distilled` count in the headline.
