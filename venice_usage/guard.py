"""The single door a Venice chat client writes usage through.

Why this module exists
----------------------

`tests/conftest.py` redirects `VENICE_USAGE_DB` at every test, but only under
pytest. Nothing protected a *script*. On 2026-09-11 a hand-run demo built a real
`council.venice.VeniceClient` with a fake `post`, called `complete()` a dozen
times against canned replies, and put thirteen synthetic rows into
`~/.local/state/venice-usage/ledger.db`. They had to be found and deleted by
hand — in a table that is the only record of what this account has spent.

The obvious fix — "remember to set VENICE_USAGE_DB before running a demo" — is
not a guard. It is a thing to forget, and it was forgotten by someone who knew
about it. A guard has to be something the careless path runs into, not
something the careful path opts into.

The rule
--------

**An injected transport means the call was not real, so its usage may not be
written to the production ledger.** The client already knows whether its
transport is the genuine `requests.post`; nobody has to remember anything, and
there is no flag to leave unset. A fake transport plus the production ledger is
refused with one line on stderr naming the fix. A fake transport plus any other
ledger is fine — that is a test or a demo doing the right thing, and its rows
are useful.

`transport_is_real` is a required keyword with no default. A future third
client cannot copy `_log_usage` and quietly inherit the hole, because the call
does not run without an answer to the question. A caller that genuinely wraps a
live transport — a budget cap, a retry shim, an instrumented session — passes
`transport_is_real=True` at the one place that knows, and it reads as the
deliberate assertion it is.

What this does NOT catch, stated so nobody assumes otherwise: a script that
monkeypatches `requests.post` at module level instead of injecting, or one that
calls `venice_usage.append()` directly with made-up numbers. Both are visible,
deliberate acts against the ledger rather than a side effect of wiring up a
fake; the incident this guards against was the second kind.
"""
from __future__ import annotations

import sys

from .ledger import append, default_db, is_production_db

#: One warning per source per process. A demo loop makes a hundred fake calls;
#: a hundred identical lines would train the reader to skip them.
_WARNED: set[str] = set()


def _warn_once(source: str, db) -> None:
    if source in _WARNED:
        return
    _WARNED.add(source)
    print(f"venice-usage: refusing to write {source} usage to the production "
          f"ledger ({db}): this client has an injected transport, so the call "
          f"was not real and neither is the row it would write. Point "
          f"VENICE_USAGE_DB at a throwaway file, or pass "
          f"transport_is_real=True if the transport really does call Venice.",
          file=sys.stderr)


def log_client_call(*, data, model, project, task_type, source,
                    transport_is_real, db_path=None) -> int:
    """Append one chat call's usage from a raw Venice response envelope.

    Returns the new rowid, or 0 when nothing was written (refused, or an
    `ext_id` collision inside `append`). `transport_is_real` has no default on
    purpose — see the module docstring."""
    if not transport_is_real and is_production_db(db_path):
        _warn_once(source, db_path or default_db())
        return 0
    usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
    return append(project=project, task_type=task_type, model=model,
                  tokens_in=usage.get("prompt_tokens") or 0,
                  tokens_out=usage.get("completion_tokens") or 0,
                  source=source, db_path=db_path)


def transport_is_real(post, real_post) -> bool:
    """Default answer for a client constructor: only the genuine transport, or
    nothing at all (which means the genuine transport), counts as real."""
    return post is None or post is real_post
