# Council v0.4.0 golden-set regression harness

This tool reruns two historical, known-false pull request fixtures through council v0.4.0. It is a regression detector. It does not estimate whether one review design is better than another, and one stochastic run does not guarantee the result of the next run.

Run this golden set before tagging a council release. The expected result for all four matrix cells is zero blocking findings:

| Fixture | Source | Fixed base | Fixed head | Arms |
|---|---|---|---|---|
| `aris-pr1` | aris-management-website PR 1 | `489b60706923e0cc9bdc529863e3b932c51e459f` | `0595eaf9fc58ed3abb7ec2311c2b649fd6130463` | no context, full-file context |
| `stw-pr11` | swimtrack-website PR 11 | `95c87b723454e7f88dffa241d6cb6a5d40de5e6a` | `eba8b54037228994c883f5559ae9b24c3316954b` | no context, full-file context |

Each fixture commits its text diff, the relevant text files from the fixed head, and a manifest with SHA-256 and byte-size evidence. `.gitattributes` marks every fixture path as `-text`, so Git does not rewrite hashed bytes when `core.autocrlf` is enabled. The diff-specific whitespace exemption remains because unified diffs require blank context markers.

Context gathering reads changed text files and the root anchors `package.json`, `.gitignore`, and `.nvmrc` when present. It caps each file at 40,000 bytes and the full context at 160,000 bytes. Binary files are not added to full-file context.

## Zero-cost validation

```bash
python3 tools/regress/run.py --dry-run --output /tmp/council-v040-dry-run.json
```

The dry run validates provenance fields, fixture hashes, snapshot membership, context construction, and the four-cell matrix. It creates no virtual environment and makes zero API calls.

## Paid run

A live run sends the committed diffs and, in the grounded arms, committed full-file snapshots to Venice. Confirm that the current task authorizes sending this source before running it. Both source repositories were private when this harness was rebuilt on 2026-09-07.

```bash
python3 tools/regress/run.py \
  --paid \
  --estimate-diem 1.0 \
  --transport-hook /absolute/path/to/budget_transport.py:post \
  --output tools/regress/results/$(date -u +%Y%m%dT%H%M%SZ).json
```

Both `--paid` and a finite positive `--estimate-diem` are mandatory. Replace the example estimate with the operator's estimate from current pricing and budget planning. The preflight labels it as an operator estimate, not a billing cap. The historical 2026-07-23 run cost about 0.72 DIEM, but the harness does not present that stale value as a current estimate. Use a capped transport hook to enforce the actual limit.

The coordinator creates a temporary venv, verifies that `council-v0.4.0` still resolves to commit `4a01298dcce2734115ac57f02592146969d76f48`, installs that recorded commit, verifies distribution version `0.4.0`, and calls `council.review.run_pr_review` for each arm. The optional transport hook is loaded from an absolute Python file and passed to `VeniceClient` as its HTTP `post` function.

Without a transport hook, the runner resolves `VENICE_COUNCIL_KEY`, then `VENICE_API_KEY`, first from the environment and then from assignment lines in `~/.env`. It never prints the key. A transport hook may enforce its own credential policy, but the pinned client still requires one of those variables.

The JSON evidence stores the pinned version, fixture base and head, grounding arm, complete review body, blocking count, unavailable flag, exception text, and final verdict. A blocking mismatch, unavailable review, or exception fails the full run. Exceptions do not stop later cells, so a failed run still records all four cells.

Bootstrap failures also write a failing JSON artifact when the output location is writable. Setup failures mark the matrix of loaded fixtures failed; failures before fixtures load, including venv and pip errors, have an empty `runs` list and a top-level `bootstrap_error`. Key resolution, transport loading, or pinned-package mismatch occurs after the golden set validates and therefore marks all four cells failed. Consumers must inspect the top-level verdict and bootstrap error; an empty failed run is not a completed four-cell comparison. If evidence cannot be written (for example, a full disk), the command fails with the underlying I/O error. Exact known credential values are redacted from stored errors; if credential lookup or exception formatting fails, error details are omitted. This is not a general detector for encoded or partial secrets. If a worker exits before it can write evidence, the coordinator replaces missing, stale, malformed, or successful prior output with a worker-startup failure artifact.

Evidence writes use a temporary file and rename. Use a different `--output` path for each concurrent run; shared output paths are not supported.

The original 2026-07-23 session scratchpad and response bodies were reaped. The result table in `docs/council-regression-2026-07-23.md` is the surviving record of that run. New result JSON belongs under `tools/regress/results/`, which is ignored except for its placeholder.
