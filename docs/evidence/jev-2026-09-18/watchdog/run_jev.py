"""Run Jev over the redacted real-log windows. Question wording was fixed before any labels were seen."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from test_run import URL, load_key  # noqa: E402

MODEL = "jev-1.13.0"
QUESTIONS = {
    "needs_human": {
        "type": "noul",
        "instructions": "This is a window of lines from a scheduled job's log. Does it show a job failure that a person should look at?",
        "criteria": {
            "true": "A job crashed, gave up, timed out, exited with a non-zero code, could not reach a service it needs, "
                    "could not deliver its output, or did not complete its work",
            "false": "The jobs completed their work. Counters equal to zero (failed=0, \"error\": null), errors that an automatic "
                     "retry fixed, error words that appear only inside titles, story text, file names or data, a job that reports "
                     "an upstream source was down but completes normally, and normal skipped, held or deferred outcomes are not failures",
        },
    },
    "latest_run_failed": {
        "type": "noul",
        "instructions": "Look only at the last run visible at the end of this log window. Did that last run end in failure?",
        "criteria": {
            "true": "The last run crashed, gave up, timed out, or exited with a non-zero code",
            "false": "The last run completed its work, or the window ends without any sign that the last run failed",
        },
    },
}


def ask(key, row):
    body = json.dumps({"state": row["text"], "model": MODEL, "questions": QUESTIONS}).encode()
    for attempt in range(4):
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "jev-test/0.1"})
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                d = json.loads(resp.read())
            return {"id": row["id"], "seconds": round(time.perf_counter() - t, 3),
                    "needs_human": d["answers"]["needs_human"]["noul"],
                    "latest_run_failed": d["answers"]["latest_run_failed"]["noul"],
                    "input_tokens": d["usage"]["input_tokens"], "model": d["model"]}
        except urllib.error.HTTPError as err:
            if err.code in (429, 529, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {"id": row["id"], "error": err.code, "detail": err.read().decode()[:300]}
        except Exception as exc:  # network
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {"id": row["id"], "error": str(exc)[:200]}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    rows = [json.loads(l) for l in open(os.path.join(here, "windows.jsonl"))]
    key = load_key()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(lambda r: ask(key, r), rows))
    wall = time.perf_counter() - t0
    json.dump(res, open(os.path.join(here, "jev_results.json"), "w"), indent=1)
    ok = [r for r in res if "error" not in r]
    secs = sorted(r["seconds"] for r in ok)
    tok = sum(r["input_tokens"] for r in ok)
    print(f"ok={len(ok)} errors={len(res) - len(ok)} wall={wall:.1f}s")
    if ok:
        print(f"latency median={secs[len(secs)//2]:.2f}s p95={secs[int(len(secs)*.95)]:.2f}s max={secs[-1]:.2f}s")
        print(f"input tokens={tok} cost=${tok * 0.042 / 1e6:.5f}")
    for r in res:
        if "error" in r:
            print("ERR", r)


if __name__ == "__main__":
    main()
