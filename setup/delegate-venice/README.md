# Shared Venice delegation routes

Owner request: use the new second-opinion key for reviews, and the code-helper key
for useful coding assistance. This small adapter reuses Council and the existing
usage ledger. It adds no daemon, scheduler, tool-execution loop, or budget drain.

`venice.md` is the maintained owner policy. `venice_delegate.py` dispatches a
single tool-free helper or invokes the existing Council review with a child-only
credential override. Council, CI, and other project credentials are unchanged.
The CLI reports an error for incomplete single-helper output and does not retry
or borrow another role's key. Council retains its established panel behavior.

## Install and check

From this repository, using the existing Council environment:

```sh
python3 setup/delegate-venice/install.py
python3 setup/delegate-venice/install.py --apply
venice-delegate --help
venice-delegate council-review --help
python3 setup/delegate-venice/install.py
```

The last command must report `No changes`. Installation copies the runtime into
`~/.local/lib/venice-delegate`, installs its launcher, and adds a marked owner
policy pointer to the global agreement and both active Delegate skills. It reads
Claude's installed-plugin metadata, not an arbitrary cached version. The global
policy still applies after a plugin update; rerun installation to refresh the
pointer in a replaced plugin skill. Existing profile configuration is preserved.

This installer targets the existing Linux VPS and its existing pipx Council
environment. It does not install Python packages or support other operating
systems. Apply holds a local lock, installs the helper file and launcher before policy pointers,
and saves a manifest plus every original file before replacing any target.
Files are replaced atomically one at a time; this is not a machine-wide transaction.
An interrupted install can be rerun. Existing bytes are backed up under
`~/.local/state/venice-delegate/backups/<timestamp>/`. To roll back, restore only
the affected files from that backup after checking for later user edits. Restore
their original permissions from `manifest.json`. Remove
new runtime files and the marked policy blocks if no prior versions existed.
No secrets are copied into the repository or runtime source.

```sh
python -m pytest tests/test_venice_delegate.py -q
```

Offline checks cover credential isolation, literal environment parsing, request
bounds, secret scrubbing, no retry/redirect, incomplete results, and Council exit
status propagation. Live validation should use one useful bounded helper task
and the required review, not a synthetic batch just to consume DIEM.

API references checked on 2026-09-13:
[chat completions](https://docs.venice.ai/api-reference/endpoint/chat/completions),
[rate limits and balances](https://docs.venice.ai/api-reference/endpoint/api_keys/rate_limits).
Models and key settings are verified against the authenticated API; documentation
alone does not prove account access. USD=0 is a key setting, while token ceilings
bound output; neither is a separate reservation of the shared DIEM balance.
