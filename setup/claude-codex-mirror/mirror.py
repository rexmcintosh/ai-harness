#!/usr/bin/env python3
"""Bounded wrapper for Codex's native Claude configuration importer."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class AppServerError(RuntimeError):
    pass


def rpc(method: str, params: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
    """Call one Codex app-server method over its newline-delimited JSON transport."""
    messages = [
        {
            "method": "initialize",
            "id": 0,
            "params": {
                "clientInfo": {
                    "name": "claude_codex_mirror",
                    "title": "Claude to Codex mirror",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "initialized", "params": {}},
        {"method": method, "id": 1, "params": params},
    ]
    try:
        process = subprocess.Popen(
            ["codex", "app-server", "--strict-config", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise AppServerError(f"Cannot start Codex app-server: {error}. Check that codex is installed and on PATH.") from error
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    stdout_buffer = bytearray()
    stderr_tail = bytearray()

    def failure(reason: str) -> AppServerError:
        detail = stderr_tail.decode("utf-8", errors="replace").strip()
        return AppServerError(reason + ("\n" + detail if detail else ""))

    try:
        def send(message):
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
            process.stdin.flush()
        send(messages[0])
        initialized = False
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            events = selector.select(remaining)
            if not events:
                break
            for key, _ in events:
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    stderr_tail.extend(chunk)
                    del stderr_tail[:-8192]
                    continue
                stdout_buffer.extend(chunk)
                while b"\n" in stdout_buffer:
                    line, _, rest = stdout_buffer.partition(b"\n")
                    stdout_buffer[:] = rest
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        raise failure("Codex app-server returned invalid JSON") from error
                    if not isinstance(message, dict):
                        raise failure("Codex app-server returned a non-object response")
                    if message.get("id") == 0:
                        if "error" in message:
                            raise failure("Codex app-server initialization failed: " + str(message["error"]))
                        if "result" not in message:
                            raise failure("Codex app-server initialization has no result")
                        if not initialized:
                            send(messages[1])
                            send(messages[2])
                            initialized = True
                        continue
                    if message.get("id") != 1 or not initialized:
                        continue
                    if "error" in message:
                        raise failure(str(message["error"]))
                    if "result" not in message:
                        raise failure("Codex app-server response has no result")
                    return message["result"]
        raise failure("Codex app-server returned no response before timeout or exit")
    except (BrokenPipeError, OSError) as error:
        raise failure("Codex app-server transport failed: " + str(error)) from error
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()



def detect(include_home: bool, cwds: list[str]) -> dict[str, Any]:
    return rpc(
        "externalAgentConfig/detect",
        {
            "includeHome": include_home,
            "cwds": cwds,
            "migrationSource": "claude-code",
            "maxSessions": 0,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-home", action="store_true")
    parser.add_argument("--cwd", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = detect(args.include_home, [str(Path(cwd).resolve()) for cwd in args.cwd])
    except AppServerError as error:
        print(str(error), file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
