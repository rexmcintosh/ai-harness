---
name: using-superpowers
description: Use when starting any conversation - establishes how to find and use skills
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, ignore this skill.
</SUBAGENT-STOP>

## The Rule

Before starting a task, check whether an available skill covers it. If one does, invoke it before responding or acting, and say which skill you're using. Skills evolve — read the current version rather than working from memory. If a skill turns out to be wrong for the situation, you don't have to follow it.

## Skill Priority

When multiple skills apply, process skills (brainstorming, systematic-debugging, writing-plans) come first — they set the approach; implementation skills carry it out. For new feature work, prefer superpowers:brainstorming before entering plan mode.

## Platform Adaptation

If your harness appears here, read its reference file for special instructions:

- Codex: `references/codex-tools.md`
- Pi: `references/pi-tools.md`
- Antigravity: `references/antigravity-tools.md`

## User Instructions

User instructions (CLAUDE.md, AGENTS.md, GEMINI.md, etc, direct requests) take precedence over skills, which in turn override default behavior. Only skip skill workflows or instructions when your human partner has explicitly told you to.
