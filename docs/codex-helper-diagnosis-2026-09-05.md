# Codex helper diagnosis

Date: 2026-09-05. Shared-backlog item:
`2026-09-05-codex-helper-sandbox-broken-vps`.

## Result and practical meaning

The direct bubblewrap probe reproduces the reported host-level failure. Two
tool-free wrapper calls work. A wrapper call requiring a shell tool did not
produce a usable result before the investigation was stopped, so end-to-end
tool execution remains unresolved and **no repair is validated**.

The operating goal is an independent reviewer that can inspect evidence and
return a usable judgment. A fluent answer to "say ok" proves neither file access
nor a trustworthy review. Keep that distinction in future helper health checks.

## Evidence

The installed wrapper inspected was:

`~/.claude/plugins/cache/authority-hacker-plugins/delegate/0.4.0/skills/codex/scripts/codex_chat.py`

| Check | Observation | What it proves |
|---|---|---|
| `bwrap --dev-bind / / --unshare-net true` | Exit 1, `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` | The supplied bubblewrap namespace setup fails in this execution environment. |
| Host/package checks | Ubuntu 24.04.4; bubblewrap 0.9.0; Codex CLI 0.153.4 | Installed versions observed during this investigation, not a recommendation to upgrade. |
| Process capability read | Effective capability mask is zero | A restriction signal; this alone does not identify the exact kernel, container, or AppArmor policy responsible. |
| `codex doctor` | Helper reports valid auth, connectivity, and consistent installation | The failure is not explained by missing authentication or a missing CLI in those checks. |
| Requested `say ok` payload, twice | Both exit 0, `success: true`, response `ok`, empty stderr | The outer JSON input and tool-free inference path work for these two runs. |
| Shell `pwd` smoke | A 90-second wrapper timeout was requested, but the supervising execution was interrupted after 562 seconds without returned output | No trustworthy shell result. The timeout/child-process behavior needs separate confirmation. The helper reports no matching test process remained. |

The shell-smoke evidence is a helper report; its temporary output was not
preserved. Do not use it to claim a specific Codex tool failed, that a timeout
guarantee works, or that the complete helper path is healthy.

The parent independently read `build_command` and `run_codex` in the installed
wrapper. Chat calls explicitly pass `-s read-only`; review and resumed chat calls
set the corresponding read-only configuration. The child process receives
`stdin=subprocess.DEVNULL`. The hypothesis that the wrapper accidentally leaves
its parsed outer JSON pipe open to the child is not supported by this code.
Historical "Reading additional input from stdin" errors remain a separate,
unresolved symptom; two successful calls do not explain every historical failure.

The current `~/.codex/config.toml` already contains an owner-commented
`sandbox_mode = "danger-full-access"` setting dated September 5. The installed
wrapper explicitly selects read-only mode instead. No setting or wrapper was
changed in this investigation. The comment is recorded as existing local state,
not treated as a fresh grant of authority or a verified explanation of the host.

## Official guidance and its limit here

OpenAI documents that Linux Codex selects `bwrap` from PATH and needs platform
support for the sandbox's namespace operations. For Ubuntu 24.04 it describes a
specific AppArmor profile as preferable to disabling the restriction globally.
This is a possible diagnostic direction, not proof that AppArmor caused this
particular loopback error. [Official OpenAI sandbox documentation](https://learn.chatgpt.com/docs/sandboxing).

## Recommended next action

Keep the wrapper's current read-only boundary. Before proposing any host change,
capture one bounded shell-tool run with a parent-enforced process-group timeout,
separate stdout/stderr, and a check that the test children terminate. Do not allow
the test to retry without sandboxing. Use a temporary working directory with a
known harmless fixture and require its exact contents in the answer. Set the
parent deadline to 90 seconds per attempt, send TERM to the child process group
at the deadline, and KILL remaining group members after five more seconds.
Require cleanup and captured results within 120 seconds total per attempt.
Run exactly two fixture attempts and one deliberate timeout fixture, with no
automatic retries. Report each attempt separately; a timeout is a failed check,
not permission to increase the limit or relax the boundary.

Then identify the failing namespace operation using local policy and audit
evidence. Compare the actual Codex sandbox invocation with the direct failing
probe; do not assume the two command lines are identical. If a narrow profile or
host capability repair is justified, present the exact change and rollback for
owner approval. A host-wide sandbox relaxation is not an automatic fallback.

Acceptance: the exact intended helper route reads the fixture and runs `pwd`
twice under the requested boundary; its logs prove the tools executed; a timeout
fixture leaves no children; the two tool-free calls still pass. Until then,
record the task as diagnosed but unresolved and use a separately verified review
route for important work. No new service, software installation, sandbox change,
or external action is authorized by merging this diagnostic document.
