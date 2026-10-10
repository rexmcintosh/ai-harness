"""MeetTrack "expected but missing" monitor: the I/O around the pure model.

    python3 -m watchdog.expect_run              # one run: evaluate, alert, investigate
    python3 -m watchdog.expect_run --dry-run    # read-only: print what it would send
    python3 -m watchdog.expect_run --narrate F  # (spawned) AI narrative for one file

Started by watchdog/run-expect.sh from cron every 5 minutes. One run:

1. reads a ``Snapshot``: a tiny database probe with a short timeout, then (only
   if that answered) the Portugal slice of meet_registry, the matching meets
   rows, per-event counts and today's result count for live meets only, the
   process list. No full-table counts.
2. evaluates expectations (watchdog/expectations.py) and the alert policy
   (watchdog/expect_alerts.py).
3. for each NEW alert (at most ``max_investigations``) runs the deterministic
   investigation and writes it under logs/investigations/.
4. sends one direct Telegram message through bin/tg-send (no model involved).
5. saves state, releases the run lock, and only then may spawn the optional AI
   narrative as a detached, time-capped child that never holds the run lock.

Nothing here writes to the database.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tomllib

from .expectations import (Snapshot, _thin, evaluate, hm_day, in_racing_window, iso_epoch,
                           make_config, meet_zone, valid_sid, writer_lines, _zone)
from .expect_alerts import POLICY_DEFAULTS, plan, render
from .expect_investigate import Probes, investigate, write_investigation

BASE = Path(os.environ.get("WATCHDOG_BASE", "/home/dev/projects/ai-harness"))
# One destination for every alert; run-expect.sh reads the same variable.
CHAT_ID = os.environ.get("WATCHDOG_CHAT_ID", "7735693897")
SPLASH_LOGS = Path(os.environ.get("WATCHDOG_SPLASH_LOGS", "/home/dev/projects/splash_poller/logs"))
LIVE_PAGE = "https://live.swimrankings.net/{sid}/"

RUN_DEFAULTS = {
    "enabled": True,
    "db_timeout_s": 8,
    "read_timeout_s": 15,
    "max_investigations": 3,
    "investigation_budget_s": 40,
    "ai_narrative": False,
    "ai_timeout_s": 300,
    "ai_model": "haiku",
    "send_timeout_s": 60,
}

_REGISTRY_COLS = ("sr_meet_id,name,city,nation,start_date,end_date,feed_type,ingest_status,"
                  "last_ingest_at,updated_at,events_published,events_with_results,"
                  "last_tick_errors,coverage_at,entries_requested_at,entries_ingested_at,"
                  "dispatch_paused,listed")


# ---------------------------------------------------------------------------
# config / state
# ---------------------------------------------------------------------------

def load_config(path: Path | None = None) -> tuple[dict, dict]:
    """(model config, raw [expect] table incl. runner and policy keys)."""
    path = path or BASE / "watchdog" / "monitors.toml"
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    table = dict(raw.get("expect") or {})
    run_cfg = {**RUN_DEFAULTS, **POLICY_DEFAULTS, **table}
    sb = raw.get("supabase") or {}
    run_cfg.setdefault("url", sb.get("url", ""))
    run_cfg.setdefault("key_env", sb.get("key_env", "SUPABASE_SERVICE_ROLE_KEY"))
    model_keys = {k: v for k, v in table.items() if k not in RUN_DEFAULTS and k not in POLICY_DEFAULTS}
    return make_config(model_keys), run_cfg


def load_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(data, indent=2, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


# ---------------------------------------------------------------------------
# reading the world (read-only)
# ---------------------------------------------------------------------------

class Rest:
    """Minimal read-only PostgREST client. Every call has a timeout and returns
    (data, error_text); it never raises."""

    def __init__(self, url: str, key: str, session=None):
        self.url, self.key = url.rstrip("/"), key
        self.session = session

    def _http(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
        return self.session

    def get(self, path: str, timeout: float, count: bool = False):
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        if count:
            headers.update({"Prefer": "count=exact", "Range": "0-0"})
        try:
            r = self._http().get(f"{self.url}/rest/v1/{path}", headers=headers, timeout=timeout)
        except Exception as exc:                 # noqa: BLE001
            return None, f"{type(exc).__name__}"
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code} {r.text[:120]}"
        if count:
            rng = r.headers.get("Content-Range", "")
            total = rng.rsplit("/", 1)[-1] if "/" in rng else ""
            return (int(total) if total.isdigit() else None), None
        try:
            return r.json(), None
        except ValueError:
            return None, "unreadable JSON"


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ps() -> str:
    try:
        return subprocess.run(["ps", "-eo", "pid,etime,args"], capture_output=True, text=True,
                              timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def processes_by_meet(ps_text: str, ids) -> dict:
    return {sid: writer_lines(ps_text, sid) for sid in ids}


def build_snapshot(now: float, cfg: dict, run_cfg: dict, rest: Rest, ps=_ps) -> Snapshot:
    t0 = time.monotonic()
    _, err = rest.get("meet_registry?select=sr_meet_id&limit=1", timeout=run_cfg["db_timeout_s"])
    latency = time.monotonic() - t0
    if err is not None:
        return Snapshot(now=now, db_ok=False, db_latency_s=latency, db_error=err)
    snap = Snapshot(now=now, db_ok=True, db_latency_s=latency)
    tz = _zone("Europe/Lisbon")
    today = datetime.fromtimestamp(now, tz=tz).date()
    nations = ",".join(cfg["nations"])
    lo = (today - timedelta(days=1)).isoformat()
    hi = (today + timedelta(days=2)).isoformat()
    rows, err = rest.get(f"meet_registry?select={_REGISTRY_COLS}&nation=in.({nations})"
                         f"&end_date=gte.{lo}&start_date=lte.{hi}&order=start_date",
                         timeout=run_cfg["read_timeout_s"])
    if rows is None:
        snap.registry_error = err
        return snap
    snap.registry = rows
    live = []
    for r in rows:
        if not valid_sid(r.get("sr_meet_id")):
            continue                     # evaluate() reports it; never used in a URL or path
        mtz = meet_zone(cfg, r.get("nation"))
        day = datetime.fromtimestamp(now, tz=mtz).date().isoformat()
        if (r.get("start_date") or "") <= day <= (r.get("end_date") or r.get("start_date") or ""):
            if r.get("feed_type") in ("pdf", "fragment", "lenex"):
                live.append((str(r["sr_meet_id"]), mtz, day))
    t_read = run_cfg["read_timeout_s"]

    def failed(sid, what, err):
        snap.read_errors.setdefault(sid, []).append(f"{what}: {err}")

    if live:
        ids = ",".join(f"sr-{sid}" for sid, _, _ in live)
        meets, err = rest.get(f"meets?select=id,live_rankings_id,status,start_date,end_date,nation"
                              f"&live_rankings_id=in.({ids})", timeout=t_read)
        if meets is None:
            for sid, _, _ in live:
                failed(sid, "meets", err)
        by_lr = {m.get("live_rankings_id"): m for m in (meets or [])}
        for sid, mtz, day in live:
            m = by_lr.get(f"sr-{sid}")
            if not m:
                continue                 # no meets row yet: nothing seeded, nothing written
            snap.meets[sid] = m
            evs, err = rest.get(f"events?select=id,event_number,session_number,day_time,relay_count,"
                                f"results(count),heats(count)&meet_id=eq.{m['id']}&order=event_number",
                                timeout=t_read)
            if evs is None:
                failed(sid, "events", err)
            snap.events[sid] = [_event(e) for e in (evs or [])]
            midnight = datetime.fromisoformat(day).replace(tzinfo=mtz).timestamp()
            n, err = rest.get(f"results?select=id&meet_id=eq.{m['id']}&created_at=gte.{_utc(midnight)}",
                              timeout=t_read, count=True)
            if n is None:
                failed(sid, "results today", err or "no count")
            snap.results_today[sid] = n or 0
            thin = _thin(snap.events[sid], cfg)
            if thin and all(e.get("id") for e in thin):
                ids_in = ",".join(e["id"] for e in thin)
                newest, _ = rest.get(f"results?select=created_at&event_id=in.({ids_in})"
                                     f"&order=created_at.desc&limit=1", timeout=t_read)
                if newest:               # optional refinement: a failure only costs precision
                    snap.thin_last_write[sid] = iso_epoch(newest[0].get("created_at"))
        snap.processes = processes_by_meet(ps(), [sid for sid, _, _ in live])
    cutoff = (today - timedelta(days=int(cfg["stale_active_days"]))).isoformat()
    stale, _ = rest.get(f"meets?select=live_rankings_id,name,nation,end_date&status=eq.ACTIVE"
                        f"&end_date=lt.{cutoff}&order=end_date", timeout=run_cfg["read_timeout_s"])
    snap.stale_active = stale or []
    return snap


def _event(e: dict) -> dict:
    def n(v):
        if isinstance(v, list) and v:
            return int(v[0].get("count") or 0)
        return 0
    return {"event_number": e.get("event_number"), "session_number": e.get("session_number"),
            "id": e.get("id"),
            "day_time": e.get("day_time"), "relay_count": e.get("relay_count") or 1,
            "results": n(e.get("results")), "entries": n(e.get("heats"))}


def real_probes() -> Probes:
    def tail(sid: str):
        if not valid_sid(sid):
            raise ValueError(f"not a meet id: {sid!r}")
        p = (SPLASH_LOGS / f"poller-{sid}.log").resolve()
        if not p.is_relative_to(SPLASH_LOGS.resolve()):
            raise ValueError("log path outside the splash_poller logs")
        try:
            with p.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 65536))
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return None

    def sup():
        p = SPLASH_LOGS / "supervise.cron.log"
        try:
            return p.read_text(errors="replace")[-32768:]
        except OSError:
            return None

    def page(sid: str):
        if not valid_sid(sid):
            raise ValueError(f"not a meet id: {sid!r}")
        import requests
        r = requests.get(LIVE_PAGE.format(sid=sid), timeout=10,
                         headers={"User-Agent": "Mozilla/5.0 (MeetTrack monitor)"})
        r.raise_for_status()
        return r.text

    return Probes(ps=_ps, read_log_tail=tail, fetch_page=page, supervise_tail=sup)


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------

def tg_send(text: str, timeout: float = 60) -> str:
    """Direct Telegram through bin/tg-send. 'accepted' | 'failed' | 'uncertain'."""
    sender = os.environ.get("WATCHDOG_TG_SEND", str(BASE / "bin" / "tg-send"))
    env = {**os.environ, "TG_SEND_RECEIPT_OUTPUT": "1"}
    try:
        r = subprocess.run([sender, CHAT_ID, "-"], input=text, text=True, capture_output=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "uncertain"
    except OSError:
        return "failed"
    if r.returncode == 0:
        return "accepted"
    return "uncertain" if r.returncode == 3 else "failed"


# ---------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------

def run_once(now: float, cfg: dict, run_cfg: dict, state: dict, snap: Snapshot, *,
             probes: Probes, sender, inv_dir: Path, dry_run: bool = False) -> dict:
    """Pure-ish core of a run, with every effect injected. Returns
    {"expectations", "notices", "text", "state", "delivery", "investigations"}."""
    exps, mem = evaluate(snap, cfg, state.get("memory"))
    racing = in_racing_window(now, {**cfg})
    notices, alerts = plan(exps, state.get("alerts") or {}, now, run_cfg, racing)
    by_id = {e.id: e for e in exps}
    written = []
    budget = int(run_cfg.get("max_investigations", 3))
    for n in notices:
        if n.kind != "new" or budget <= 0 or n.id not in by_id:
            continue
        budget -= 1
        try:
            inv = investigate(by_id[n.id], snap, probes,
                              budget_s=float(run_cfg.get("investigation_budget_s", 40)))
        except Exception as exc:                          # noqa: BLE001
            inv = {"summary": f"investigation failed: {type(exc).__name__}", "sections": [],
                   "facts": {}}
        n.investigation = {"summary": inv["summary"]}
        if not dry_run:
            try:
                path = write_investigation(inv_dir, by_id[n.id], inv, now)
                n.investigation["path"] = str(path)
                written.append(str(path))
            except OSError as exc:
                n.investigation["path"] = f"(not written: {exc})"
        else:
            n.investigation["sections"] = inv["sections"]
    text = render(notices, now) if notices else ""
    delivery = "none"
    new_state = {"memory": mem, "alerts": alerts, "last_run": now}
    if notices and not dry_run:
        delivery = sender(text)
        if delivery == "failed":
            # Definitely not delivered: keep the old alert state so the next
            # run sends the same news again. Evaluator memory still advances.
            new_state["alerts"] = state.get("alerts") or {}
    new_state["last_delivery"] = {"at": now, "status": delivery, "notices": len(notices)} \
        if notices else (state.get("last_delivery") or {})
    return {"expectations": exps, "notices": notices, "text": text, "state": new_state,
            "delivery": delivery, "investigations": written, "racing": racing}


def format_dry_run(result: dict, now: float) -> str:
    tz = _zone("Europe/Lisbon")
    stamp = datetime.fromtimestamp(now, tz=tz).strftime("%a %d %b %Y %H:%M Lisbon")
    lines = [f"MeetTrack expectations dry-run, {stamp} "
             f"(racing hours: {'yes' if result['racing'] else 'no'})", ""]
    rank = {"missed": 0, "unknown": 1, "pending": 2, "closed": 3, "met": 4, "n/a": 5}
    exps = sorted(result["expectations"],
                  key=lambda e: (e.severity == "info", rank.get(e.status, 9), e.meet or "", e.id))
    for e in exps:
        tag = (f"NOTE" if e.severity == "info" and e.status == "missed"
               else f"{e.status.upper()} {e.severity}" if e.status == "missed" else e.status)
        due = f" | due {hm_day(e.deadline, tz, now)}" if e.deadline else ""
        lines.append(f"[{tag}] {e.id}  {e.meet or ''}{due}")
        lines.append(f"    {e.evidence}")
    lines.append("")
    if result["notices"]:
        lines.append("WOULD SEND (direct Telegram):")
        lines.append(result["text"])
        for n in result["notices"]:
            if n.investigation and n.investigation.get("sections"):
                lines.append("")
                lines.append(f"--- investigation for {n.id} (dry-run: not written) ---")
                for title, body in n.investigation["sections"]:
                    lines.append(f"  {title}:")
                    lines.extend(f"    {ln}" for ln in body[:16])
    else:
        lines.append("WOULD SEND: nothing")
    return "\n".join(lines)


class RunLock:
    def __init__(self, path: Path):
        self.path, self.fh = path, None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self) -> None:
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None


_SECRET_ENV = ("SUPABASE", "KEY", "TOKEN", "SECRET", "PASSWORD", "VAPID")


def spawn_narrative(path: str, run_cfg: dict, log_dir: Path, popen=subprocess.Popen) -> bool:
    """Detached, time-capped AI narrative. Called after the run lock is released;
    the child gets no secrets from this environment and holds no run lock."""
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in _SECRET_ENV)}
    env["PYTHONPATH"] = str(BASE)
    cap = int(run_cfg.get("ai_timeout_s", 300)) + 60
    argv = ["timeout", "--kill-after=30", str(cap), sys.executable, "-m",
            "watchdog.expect_run", "--narrate", path]
    try:
        err = open(log_dir / "expect-narrative.err", "a")
        popen(argv, cwd=str(BASE), env=env, stdin=subprocess.DEVNULL, stdout=err, stderr=err,
              start_new_session=True, close_fds=True)
        return True
    except OSError:
        return False


def narrate(path: str, run_cfg: dict, log_dir: Path, *, runner=subprocess.run, sender=tg_send) -> int:
    """The optional second opinion: the Claude investigator reads the
    investigation file (and splash_poller logs) and writes a short diagnosis,
    appended to the file and sent as a follow-up. Best effort; never retried."""
    lock = RunLock(log_dir / "expect-narrative.lock")
    if not lock.acquire():
        return 0
    try:
        target = Path(path)
        body = target.read_text()
        prompt_path = BASE / "watchdog" / "prompts" / "investigate-expect.md"
        prompt = prompt_path.read_text().replace("{{INVESTIGATION}}", body).replace(
            "{{PATH}}", str(target))
        claude = os.environ.get("WATCHDOG_CLAUDE_BIN", "claude")
        try:
            r = runner([claude, "-p", prompt, "--model", run_cfg.get("ai_model", "haiku"),
                        "--allowedTools", "Read", "--output-format", "json"],
                       capture_output=True, text=True, timeout=int(run_cfg.get("ai_timeout_s", 300)))
            result = json.loads(r.stdout or "{}").get("result", "").strip()
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            result = ""
            with target.open("a") as fh:
                fh.write(f"\n## AI narrative\n\n    unavailable: {type(exc).__name__}\n")
            return 0
        if not result or result.startswith("FAILED"):
            with target.open("a") as fh:
                fh.write("\n## AI narrative\n\n    unavailable (empty or FAILED)\n")
            return 0
        with target.open("a") as fh:
            fh.write("\n## AI narrative\n\n" + result + "\n")
        sender(f"MeetTrack investigation follow-up ({target.name}):\n{result}\n\nDetails: {target}")
        return 0
    finally:
        lock.release()


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    cfg, run_cfg = load_config()
    log_dir = Path(os.environ.get("WATCHDOG_LOG_DIR", str(BASE / "watchdog" / "logs")))
    if argv[:1] == ["--narrate"] and len(argv) == 2:
        return narrate(argv[1], run_cfg, log_dir)
    dry_run = "--dry-run" in argv
    if not run_cfg.get("enabled", True) and not dry_run:
        print("expect monitor disabled in monitors.toml")
        return 0
    state_path = Path(os.environ.get("WATCHDOG_EXPECT_STATE",
                                     str(BASE / "watchdog" / "expect-state.json")))
    lock = RunLock(log_dir / "expect.lock")
    if not dry_run and not lock.acquire():
        print("expect monitor: previous run still holds the lock; skipping")
        return 0
    try:
        key = os.environ.get(run_cfg["key_env"], "")
        now = time.time()
        state = load_json(state_path)
        if not key or not run_cfg.get("url"):
            snap = Snapshot(now=now, db_ok=False, db_error=f"{run_cfg['key_env']} not set")
        else:
            snap = build_snapshot(now, cfg, run_cfg, Rest(run_cfg["url"], key))
        result = run_once(now, cfg, run_cfg, state, snap, probes=real_probes(),
                          sender=lambda t: tg_send(t, run_cfg.get("send_timeout_s", 60)),
                          inv_dir=log_dir / "investigations", dry_run=dry_run)
        if dry_run:
            print(format_dry_run(result, now))
            return 0
        save_json(state_path, result["state"])
        missed = [e.id for e in result["expectations"] if e.status == "missed" and e.severity != "info"]
        line = {"ts": _utc(now), "db_ok": snap.db_ok, "racing": result["racing"],
                "missed": missed, "notices": [f"{n.kind}:{n.id}" for n in result["notices"]],
                "delivery": result["delivery"], "investigations": result["investigations"]}
        with open(log_dir / "expect-runs.log", "a") as fh:
            fh.write(json.dumps(line) + "\n")
        print(f"expect: {len(result['expectations'])} expectations, {len(missed)} missed, "
              f"{len(result['notices'])} notices, delivery={result['delivery']}")
    finally:
        lock.release()
    if (result["delivery"] in ("accepted", "uncertain") and result["investigations"]
            and run_cfg.get("ai_narrative", False)):
        spawn_narrative(result["investigations"][0], run_cfg, log_dir)
    return 3 if result["delivery"] == "failed" else 0   # 3: send failed, retried next run


if __name__ == "__main__":
    raise SystemExit(main())
