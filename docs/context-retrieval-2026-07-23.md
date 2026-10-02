# Context-retrieval instrumentation — 2026-07-23

**What this is.** Per-file retrieval counts for the two context stores — memory
(`~/.claude/projects/-home-dev/memory/`) and wiki (`~/wiki/`) — measured across
**all 5,783 session/subagent transcripts** in `~/.claude/projects/`. This is the
instrumentation that §11 Correction A of
`harness-engineering-assessment-2026-07-20.md` requires **before** any context
retirement, and a named precondition of the §12 bake-off (it is the baseline for
the "context load" metric).

**Method.** Scan every transcript `.jsonl`; count `tool_use` blocks with
`name == "Read"` whose `file_path` targets a memory or wiki `.md` file. Zero
parse errors; scan runtime ~5 s (script: session scratchpad `retrieval_scan.py`).

**Caveats — read before retiring anything:**

- **Memory zero-Read ≠ memory never-used.** The harness auto-loads `MEMORY.md`
  every session and injects recalled memories via system-reminders — neither is a
  `Read` call. Memory counts measure *explicit agent-initiated re-reads only*.
  The memory zero-list is a *weak* signal.
- **Wiki zero-Read is a strong signal.** The wiki has no auto-injection and no
  instructed reader (`grep -ic wiki ~/.claude/CLAUDE.md` = 0), so an explicit
  `Read` is essentially the *only* retrieval path. 172/246 never-read means
  ~70% of the wiki has never been retrieved by any agent, ever.
- Access via `Bash` (`cat`/`grep`), `Edit`/`Write` (loom absorption writes), and
  the `Explore` agent's excerpt reads are not counted. Overall bias: undercount.

## Summary

| | Inventory | Ever Read | Never Read | Never-read share |
|---|---:|---:|---:|---:|
| Memory | 100 | 54 | 46 | 46% |
| Wiki | 246 | 74 | 172 | 70% |

The retrieval curve is steep: after the top handful of files, counts fall to 1–2.

## Memory — every file ever explicitly Read

| Reads | Sessions | File |
|---:|---:|---|
| 118 | 116 | `MEMORY.md` |
| 63 | 62 | `loom-distill-pipeline.md` |
| 13 | 13 | `aris-management-website.md` |
| 12 | 12 | `bebop-email-triage.md` |
| 12 | 12 | `rex-family-liam.md` |
| 11 | 11 | `bebop-briefing-mcp-connecting-failure.md` |
| 10 | 10 | `bebop-briefing-format.md` |
| 8 | 8 | `telegram-markdown-escaping.md` |
| 7 | 7 | `wiki-absorption-procedure.md` |
| 6 | 6 | `bebop-on-claude-code.md` |
| 6 | 6 | `rex-recurring-contacts.md` |
| 6 | 6 | `mesh-watchdog-log-paths.md` |
| 6 | 6 | `bebop-morning-briefing-pipeline.md` |
| 5 | 5 | `freestyle-series-pipeline.md` |
| 5 | 5 | `agents-tmux-monitor.md` |
| 4 | 4 | `rex-timezone.md` |
| 4 | 4 | `swimtrack-council-gate.md` |
| 4 | 4 | `site-flow-skills.md` |
| 3 | 3 | `harness-audit-2026-07.md` |
| 3 | 3 | `opus-subagent-dispatch-bug.md` |
| 3 | 3 | `claude-session-limit-reset.md` |
| 3 | 3 | `splash-poller-swimmer-matching.md` |
| 2 | 2 | `tmux-prefix-key.md` |
| 2 | 2 | `telegram-mcp-leak-reaper.md` |
| 2 | 2 | `astro-cloudflare-verify-before-push.md` |
| 2 | 2 | `rex-wiki-location.md` |
| 2 | 2 | `splash-poller-supabase-cap.md` |
| 2 | 2 | `vps-tailscale-mesh.md` |
| 2 | 2 | `vps-macmini-network.md` |
| 2 | 2 | `bebop-runs-log-schema.md` |
| 2 | 2 | `bebop-evening-wrapup-pipeline.md` |
| 2 | 2 | `rex-family-cai.md` |
| 1 | 1 | `Telegram Markdown escaping.md` |
| 1 | 1 | `feedback_working_style.md` |
| 1 | 1 | `council-v040-architecture.md` |
| 1 | 1 | `feedback-full-chain-autonomy.md` |
| 1 | 1 | `bebop-placeholder-send-failure.md` |
| 1 | 1 | `synquery-consulting-lead.md` |
| 1 | 1 | `procedure-gh-merge-worktree.md` |
| 1 | 1 | `solviva-solar-project.md` |
| 1 | 1 | `joao-cheira-ois.md` |
| 1 | 1 | `wiki-gdoc-absorption-limit.md` |
| 1 | 1 | `heron-creek-teaser-pipeline.md` |
| 1 | 1 | `venice-image-edit-contract.md` |
| 1 | 1 | `feedback-proactive-notification.md` |
| 1 | 1 | `telegram-subagent-fallback.md` |
| 1 | 1 | `feedback-check-config-before-recommending.md` |
| 1 | 1 | `ephemeral-worktree-cleanup.md` |
| 1 | 1 | `calendar-mcp-param-names.md` |
| 1 | 1 | `hallucinated-injection-vs-real.md` |
| 1 | 1 | `subagent-transcript-paths.md` |
| 1 | 1 | `council-review-failed-not-always-blocking.md` |
| 1 | 1 | `macos-resource-fork-vps-pollution.md` |
| 1 | 1 | `portugal-nationality-law-change.md` |
| 1 | 1 | `rex-sdvosb-federal-contracting.md` |
| 1 | 1 | `feedback-idempotent-writes-finish.md` |

*Note: `MEMORY.md`'s 118 reads are explicit re-reads on top of its per-session auto-load — it is loaded ~every session regardless.*

## Wiki — every article ever explicitly Read

| Reads | Sessions | Article |
|---:|---:|---|
| 84 | 83 | `tools/loom.md` |
| 37 | 25 | `_index.md` |
| 11 | 7 | `people/rex-mcintosh.md` |
| 11 | 11 | `projects/bebop-briefing.md` |
| 9 | 9 | `people/liam.md` |
| 9 | 9 | `projects/swimtrack.md` |
| 8 | 8 | `ABSORB_INSTRUCTIONS.md` |
| 8 | 8 | `projects/aris-management-website.md` |
| 7 | 5 | `companies/aris-management.md` |
| 7 | 6 | `projects/romance-empire.md` |
| 7 | 7 | `tools/loom-distill-pipeline.md` |
| 6 | 5 | `people/mac-mcintosh.md` |
| 6 | 6 | `patterns/loom-route-stage.md` |
| 5 | 5 | `eras/career-arc.md` |
| 5 | 5 | `projects/ai-automation-infrastructure.md` |
| 4 | 4 | `companies/united-airlines.md` |
| 4 | 4 | `places/santa-amaro-oeiras.md` |
| 4 | 4 | `places/portugal.md` |
| 4 | 4 | `philosophies/one-person-ai-operation.md` |
| 4 | 4 | `projects/calendar-mcp.md` |
| 3 | 3 | `tools/claude-ios-app.md` |
| 3 | 3 | `projects/finance-tracker.md` |
| 3 | 3 | `projects/splash-poller.md` |
| 2 | 2 | `people/gale-mcintosh.md` |
| 2 | 2 | `raw/entries/2026-06-06_portugal-nationality-law.md` |
| 2 | 2 | `projects/bebop-email-triage-query.md` |
| 2 | 2 | `patterns/feedback_working_style.md` |
| 2 | 2 | `projects/splash-poller-infra.md` |
| 2 | 2 | `projects/project-loom.md` |
| 2 | 2 | `patterns/feedback-proactive-notification.md` |
| 2 | 2 | `tools/drawbridge.md` |
| 2 | 2 | `projects/bebop-briefing-pipeline.md` |
| 2 | 2 | `patterns/wiki-absorption-workflow.md` |
| 2 | 2 | `decisions/confirm-then-merge-protocol.md` |
| 2 | 2 | `patterns/loom.md` |
| 2 | 2 | `patterns/feedback-wrong-machine-agent-invocation.md` |
| 2 | 1 | `scripts/fetch_drive_gmail.py` |
| 1 | 1 | `people/linnea-mcintosh.md` |
| 1 | 1 | `projects/marshfield-track-program.md` |
| 1 | 1 | `projects/tribute-hall.md` |
| 1 | 1 | `raw/entries/2026-06-06_macmcintosh-repo.md` |
| 1 | 1 | `projects/macmcintosh-memorial-project.md` |
| 1 | 1 | `raw/entries/2026-06-06_mcintosh-portfolio-analysis.md` |
| 1 | 1 | `raw/entries/2026-06-06_moving-to-lisbon-portugal.md` |
| 1 | 1 | `raw/entries/2026-06-06_portugal-tax-information.md` |
| 1 | 1 | `raw/entries/2026-06-06_crossfit-portugal-discussions.md` |
| 1 | 1 | `raw/entries/2026-06-06_portugal-gym-sops.md` |
| 1 | 1 | `raw/entries/2026-06-06_bullsharks-team-handbook.md` |
| 1 | 1 | `raw/entries/2026-06-06_portugal-proof-financial-means.md` |
| 1 | 1 | `raw/entries/2026-06-06_renovation-projects-slides.md` |
| 1 | 1 | `raw/entries/2026-06-06_solviva-solar-thread.md` |
| 1 | 1 | `raw/entries/2026-06-06_swim-competition-joao-cheira.md` |
| 1 | 1 | `raw/entries/2026-06-06_swim-lessons-inquiry-hector.md` |
| 1 | 1 | `raw/entries/2026-06-06_stripe-aris-review.md` |
| 1 | 1 | `raw/entries/2026-06-06_synquery-consulting-opportunity.md` |
| 1 | 1 | `raw/entries/2026-06-06_aris-management-website-repo.md` |
| 1 | 1 | `_backlinks.json` |
| 1 | 1 | `projects/project-vps-infra.md` |
| 1 | 1 | `tools/macos-bsd-tar.md` |
| 1 | 1 | `projects/freestyle-series-pipeline.md` |
| 1 | 1 | `tools/subagent-transcript-debugging.md` |
| 1 | 1 | `feedback_working_style.md` |
| 1 | 1 | `patterns/wiki-absorption.md` |
| 1 | 1 | `patterns/loom-route-output.md` |
| 1 | 1 | `tools/agents-tmux-monitor.md` |
| 1 | 1 | `decisions/versioned-release-discrete-article.md` |
| 1 | 1 | `decisions/feedback-proactive-notification.md` |
| 1 | 1 | `projects/project-council.md` |
| 1 | 1 | `tools/personal-wiki.md` |
| 1 | 1 | `tools/fidelity-brokeragelink.md` |
| 1 | 1 | `people/rex-family-cai.md` |
| 1 | 1 | `patterns/wiki-absorption-procedure.md` |
| 1 | 1 | `tools/watchdog-logroot-path-gotcha.md` |
| 1 | 1 | `decisions/harness-audit-2026-07.md` |
| 1 | 1 | `tools/site-flow-skills.md` |
| 1 | 1 | `companies/equinor.md` |
| 1 | 1 | `projects/meettrack-supervise.md` |
| 1 | 1 | `patterns/wrong-machine-agent-halt.md` |
| 1 | 1 | `patterns/wiki-route-context-sensitivity.md` |
| 1 | 1 | `tools/vps-macmini-network.md` |
| 1 | 1 | `vps-macmini-network.md` |
| 1 | 1 | `projects/telegram-setup.md` |

## Retirement docket — never explicitly Read

Candidates only — apply the caveats above. Wiki entries here are strong candidates; memory entries need the recall-injection check first.

### Memory (46 files — weak signal, verify recall-injection first)

- `bank-sales-project.md`
- `bash-sandbox-no-ops-kill.md`
- `bebop-telegram-chat-id.md`
- `claude-code-skills-vs-commands.md`
- `dataforseo-pricing.md`
- `deep-research-session-limit-gotcha.md`
- `delegate-plugin-setup.md`
- `dialectica-consulting-lead.md`
- `diem-utc-epoch.md`
- `fable-brainstorm-handoff.md`
- `feedback-explicit-single-purpose-skills.md`
- `feedback-image-file-placement.md`
- `feedback-security-remove-surface.md`
- `feedback-verify-cli-before-encoding.md`
- `finance-tracker-app.md`
- `finance-tracker-rebalance-engine.md`
- `finance-tracker-united-airlines-trap.md`
- `git-rebase-doc-fix.md`
- `meettrack-ingest-522-errors.md`
- `memory-stack-thread.md`
- `pdfplumber-column-interleave.md`
- `plain-english-explanations.md`
- `playwright-mcp.md`
- `remote-branch-audit.md`
- `rex-family-kelly.md`
- `rex-telegram-chat-id.md`
- `rex-travel-contact-barbara.md`
- `sam-gov-registration-procedure.md`
- `sdvosb-dsbs-system-of-record.md`
- `skill-authoring-style.md`
- `skill-install-auto-mode-gate.md`
- `skill-install-folder-naming.md`
- `skill-secrets-non-git-home.md`
- `splash-poller-caderno-import.md`
- `splash-poller-wiki-pending.md`
- `swimtrack-coach-vps-deploy.md`
- `swimtrack-website-image-engine.md`
- `tailscale-add-untagged-device.md`
- `telegram-reply-text-length-error.md`
- `topical-map-dfseo2.md`
- `vps-cai-shared-access.md`
- `vps-finder-sftp-gotcha.md`
- `vps-harden-mesh-only.md`
- `vps-iphone-file-access.md`
- `vps-path-convention.md`
- `wrangler-noninteractive-auth.md`

### Wiki (172 articles — strong signal)

- `agents-tmux-monitor.md`
- `bebop-assistant.md`
- `bebop-briefing-format.md`
- `bebop-briefing-mcp-connecting-failure.md`
- `bebop-email-triage.md`
- `bebop-on-claude-code.md`
- `claude-session-limit-reset.md`
- `companies/solviva.md`
- `companies/tejomed.md`
- `decisions/after-hours-trading.md`
- `decisions/bebop-brain-cutover.md`
- `decisions/bebop-vip-email-filter.md`
- `decisions/bebop-wrap-format.md`
- `decisions/briefing-tone-preference.md`
- `decisions/council-gate-chair-arbitration.md`
- `decisions/etf-config-labels.md`
- `decisions/feedback-after-hours-trading.md`
- `decisions/feedback-background-writes.md`
- `decisions/feedback-merge-protocol.md`
- `decisions/feedback_background-writes.md`
- `decisions/fleet-push-governance.md`
- `decisions/full-chain-autonomy-authorization.md`
- `decisions/harness-mcp-timing-diagnosis.md`
- `decisions/kelly-fidelity-401k-rebalance-jun-2026.md`
- `decisions/loom-cron-sync-non-fatal.md`
- `decisions/mcintosh-portfolio-jun-2026-snapshot.md`
- `decisions/merge-strategy-commit-preservation.md`
- `decisions/premarket-vs-regular-hours-trading.md`
- `decisions/ship-it-exclusive-merge-protocol.md`
- `decisions/site-flow-skill-architecture.md`
- `decisions/skill-design-explicitly-named.md`
- `decisions/skill-installation-non-git-home.md`
- `decisions/superpowers-gates-scope.md`
- `decisions/telegram-allowlist-routing.md`
- `decisions/verify-cli-syntax-before-encoding.md`
- `decisions/vps-update-reboot-sequencing.md`
- `decisions/watchdog-graceful-degradation.md`
- `decisions/wiki-absorption-incremental-run-audit.md`
- `decisions/wiki-absorption-incremental-runs.md`
- `decisions/wiki-routing-claude-ios.md`
- `feedback-infrastructure-skill-level.md`
- `feedback-opus-subagent-fallback.md`
- `feedback-opus-subagent-skill-corruption.md`
- `feedback-proactive-notification.md`
- `feedback-security-remove-surface.md`
- `feedback/communication-style.md`
- `feedback/feedback_background-writes.md`
- `feedback/infra_instruction_preference.md`
- `feedback_background-writes.md`
- `loom-distill-pipeline.md`
- `memory-stack-thread.md`
- `bebop-briefing-format.md`
- `bebop-briefing-mcp-connecting-failure.md`
- `bebop-morning-briefing-pipeline.md`
- `feedback-tooling.md`
- `loom-distill-pipeline.md`
- `mesh-watchdog-log-paths.md`
- `project-telegram-setup.md`
- `project-vps-infra.md`
- `mesh-watchdog-log-paths.md`
- `patterns/bebop-briefing-mcp-connecting-failure.md`
- `patterns/classifier-gate-edit-false-positives.md`
- `patterns/claude-session-limit.md`
- `patterns/configuration-aware-recommendations.md`
- `patterns/cross-session-continuity.md`
- `patterns/evening-wrap-up-format.md`
- `patterns/feedback_proactive-notification.md`
- `patterns/loom-distill-pipeline.md`
- `patterns/loom-session-limit-interruption.md`
- `patterns/mobile-vps-access.md`
- `patterns/scoped-commits-concurrent-worktrees.md`
- `patterns/subagent-driven-dev-static-content.md`
- `patterns/swimtrack-council-release-sequence.md`
- `patterns/tdd-rollback-monkeypatch.md`
- `patterns/transition-operator.md`
- `patterns/vscode-tmux-claude-workflow.md`
- `patterns/wiki-absorption-raw-entry.md`
- `patterns/wiki-absorption-routing.md`
- `patterns/wiki-enrichment-anti-pattern.md`
- `patterns/wiki-maintenance.md`
- `patterns/wiki-raw-entry-absorption.md`
- `patterns/wiki-routing-project-articles.md`
- `people/jessica-del-monaco.md`
- `people/joao-cheira.md`
- `people/kelly-mcintosh.md`
- `people/mia-mcintosh.md`
- `people/steve-prefontaine.md`
- `philosophies/knowledge-management.md`
- `procedures/bebop-evening-wrapup.md`
- `project-telegram-setup.md`
- `projects/aris-deploy-and-verify.md`
- `projects/bank-sales.md`
- `projects/bebop-assistant.md`
- `projects/bebop-briefing-alert-rc0-anomaly.md`
- `projects/bebop-configuration.md`
- `projects/bebop-evening-wrap.md`
- `projects/bebop-mcp-connecting-failure.md`
- `projects/bebop-morning-briefing-pipeline.md`
- `projects/bebop-telegram-delivery.md`
- `projects/council-diff-blindness-root-cause.md`
- `projects/council-gate-tdd.md`
- `projects/council-v0.4.0.md`
- `projects/council.md`
- `projects/loom.md`
- `projects/macos-resource-fork-vps-pollution.md`
- `projects/mesh-vps-infra.md`
- `projects/mesh-vps-watchdog.md`
- `projects/mesh-vps.md`
- `projects/mesh-watchdog.md`
- `projects/portfolio-rebalancer.md`
- `projects/portfolio-tracker.md`
- `projects/procurement-ai-pipeline.md`
- `projects/project-loom-v1-rollout.md`
- `projects/project-telegram-setup.md`
- `projects/rebalance-engine.md`
- `projects/site-flow-skills.md`
- `projects/splash-poller-db-write-verify.md`
- `projects/swimtrack-postgrest-page-size.md`
- `projects/swimtrack/splash-poller.md`
- `projects/tailscale-mesh.md`
- `projects/watchdog-agent.md`
- `raw/entries/2026-06-06_mac-athlete-interviews.md`
- `raw/entries/2026-06-06_mac-prefontaine-video-interview.md`
- `reference/site-flow-skills.md`
- `relationships/mac-and-rex.md`
- `relationships/rex-family-context.md`
- `skills/strategic-storytelling.md`
- `skills/topical-map-dfseo2.md`
- `swimtrack-coach-vps-deploy.md`
- `tools/bebop-evening-wrapup.md`
- `tools/bebop-logging.md`
- `tools/bebop-mcp-dispatch.md`
- `tools/calendar-mcp.md`
- `tools/claude-code-session-limits.md`
- `tools/claude-code-skill-folder-naming.md`
- `tools/claude-code.md`
- `tools/claude-session-limit-reset.md`
- `tools/claude-session-limits.md`
- `tools/claude.md`
- `tools/council.md`
- `tools/dataforseo.md`
- `tools/feedback-tooling.md`
- `tools/git.md`
- `tools/hetzner-cloud-networking.md`
- `tools/icloud-drive.md`
- `tools/loom-promote-validation.md`
- `tools/loom-rollout-env-gotchas.md`
- `tools/macos-remote-login.md`
- `tools/macos-resource-fork-cleanup.md`
- `tools/macos-resource-fork-pollution.md`
- `tools/mcp-connectivity-probe.md`
- `tools/mcp-deferred-tools.md`
- `tools/mcp.md`
- `tools/mesh-watchdog-agent.md`
- `tools/mesh-watchdog-log-paths.md`
- `tools/mesh-watchdog.md`
- `tools/supabase-postgrest.md`
- `tools/telegram-markdown-escaping.md`
- `tools/telegram-mcp.md`
- `tools/telegram-send-auth-failure.md`
- `tools/tmux-prefix-key.md`
- `tools/tmux.md`
- `tools/vite.md`
- `tools/vitest.md`
- `tools/vps-harden-mesh-only.md`
- `tools/vps-ssh-config.md`
- `tools/watchdog-eisdir-gotcha.md`
- `tools/whisper.md`
- `tools/wiki-absorption.md`
- `tools/wiki-maintenance.md`
- `tools/wrangler.md`
- `wiki/tools/loom.md`
