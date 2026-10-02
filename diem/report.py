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
