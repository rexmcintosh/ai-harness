"""Role-scoped Venice credentials for the backlog runner.

This deliberately matches the maintained ``venice-delegate`` loader: one exact
environment name per role, optional literal ``.env`` parsing, and no fallback.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex


KEYS = {"review": "VENICE_SECOND_OPINION_KEY", "code": "VENICE_CODE_HELPER_KEY"}


class VeniceKeyError(RuntimeError):
    pass


def load_key(role: str, environ=None, env_path=None) -> str:
    environ = os.environ if environ is None else environ
    try:
        name = KEYS[role]
    except KeyError:
        raise VeniceKeyError(f"unknown Venice key role: {role}") from None
    if name in environ:
        key = str(environ[name]).strip()
    else:
        key = ""
        path = Path.home() / ".env" if env_path is None else Path(env_path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        except OSError:
            raise VeniceKeyError(f"Cannot read {path}") from None
        for line in lines:
            line = line.strip().removeprefix("export ")
            lhs, sep, rhs = line.partition("=")
            if sep and lhs.strip() == name:
                try:
                    parts = shlex.split(rhs, comments=True)
                except ValueError:
                    raise VeniceKeyError(f"Malformed {name} in {path}") from None
                if len(parts) > 1:
                    raise VeniceKeyError(f"Malformed {name} in {path}")
                key = parts[0] if parts else ""
    if not key:
        raise VeniceKeyError(f"{name} is missing or empty; use the subscription fallback")
    return key
