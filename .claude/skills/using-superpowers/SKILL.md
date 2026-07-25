---
name: using-superpowers
description: Consider when a request looks like it may be covered by an installed skill - explains how to find and choose skills. Before applying any skill it points to, you MUST first ask the user whether to use that skill and proceed only if they approve. Never auto-apply a skill without asking, and never run a skill search on routine requests.
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, ignore this skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
Judge for yourself whether a skill genuinely fits the task — then ASK the user before using it.

NEVER invoke a skill workflow without the user's approval for that specific task. Their answer decides it; a "no" means do the work directly.

This is not negotiable. You cannot rationalize your way out of asking first.
</EXTREMELY-IMPORTANT>

## The Rule

**When you judge that a skill fits, ask the user first**: name the skill, say in one line what it would add and what it costs (extra steps, extra tokens/time), and let them choose. Do the work directly unless they approve.

Routine requests — small edits, direct questions, quick lookups — need no skill and no question. Do them directly.

Once approved, announce "Using [skill] to [purpose]" and follow the skill exactly. If it has a checklist, create a todo per item.

## Skill Priority

When multiple skills apply, process skills come first — they set the approach, then implementation skills (frontend-design, etc.) carry it out. Brainstorming and systematic-debugging are Superpowers' most common process skills, but the rule holds for any of them.

- "Let's build X" → propose superpowers:brainstorming, then implementation skills.
- "Fix this bug" → propose superpowers:systematic-debugging, then domain skills.

Propose the whole chain in one question rather than asking again at each stage.

## Red Flags

These thoughts mean STOP—you're rationalizing:

| Thought | Reality |
|---------|---------|
| "I'll invoke it, they'd probably say yes" | Probably isn't approval. Ask. |
| "Asking costs a turn, just run it" | An unwanted workflow costs far more. Ask. |
| "They approved this skill last time" | Approval is per task, not standing. Ask again. |
| "I remember this skill" | Skills evolve. Read current version. |
| "The user said no, but the skill knows better" | Their no is final. Do the work directly. |

## Platform Adaptation

If your harness appears here, read its reference file for special instructions:

- Codex: `references/codex-tools.md`
- Pi: `references/pi-tools.md`
- Antigravity: `references/antigravity-tools.md`

## User Instructions

User instructions (CLAUDE.md, AGENTS.md, GEMINI.md, etc, direct requests) take precedence over skills, which in turn override default behavior. Only skip skill workflows or instructions when your human partner has explicitly told you to.
