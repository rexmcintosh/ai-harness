"""Deterministic secret gate. Wraps `detect-secrets-hook` and returns
True only when the scan finds zero secrets. This is the real control; the LLM
sanitize pass is a second layer, never the gate. Uses the hook (not `detect-secrets
scan`) because the hook scans absolute paths correctly, whereas `detect-secrets scan`
silently skips paths outside the cwd. Fail-closed: any error or non-file → not clean.

Entropy plugins disabled:
  Base64HighEntropyString and HexHighEntropyString are disabled to prevent
  false positives on benign high-entropy identifiers (Gmail message IDs,
  Google Drive doc IDs, content hashes, etc.) that saturate transcripts.
  The LLM distill sanitize pass is the entropy backstop for these cases.
  Credential-specific detectors (AWSKeyDetector, GitHubTokenDetector,
  PrivateKeyDetector, JwtTokenDetector, SlackDetector, etc.) and the
  KeywordDetector remain fully active to catch real credentials."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_hook() -> Path | None:
    """Locate detect-secrets-hook: beside the running interpreter first (the
    venv install), then on PATH. None if neither — callers fail closed."""
    sibling = Path(sys.executable).parent / "detect-secrets-hook"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return sibling
    on_path = shutil.which("detect-secrets-hook")
    return Path(on_path) if on_path else None


def scan_clean(path: Path) -> bool:
    # Fail-closed: only scan actual files (not directories or non-existent paths)
    if not Path(path).is_file():
        return False  # fail-closed: nothing to scan ≠ clean

    detect_secrets_hook = find_hook()
    if detect_secrets_hook is None:
        return False  # fail-closed

    try:
        proc = subprocess.run(
            [
                str(detect_secrets_hook),
                "--json",
                "--disable-plugin", "Base64HighEntropyString",
                "--disable-plugin", "HexHighEntropyString",
                str(path),
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False  # fail-closed

    # detect-secrets-hook returns 0 if no secrets, non-zero if secrets found
    if proc.returncode == 0:
        return True  # No secrets found

    # Return code != 0 means secrets were found
    if not proc.stdout:
        return False  # Error or other issue

    try:
        results = json.loads(proc.stdout).get("results", {})
    except json.JSONDecodeError:
        return False
    return not any(results.values())
