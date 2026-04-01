---
name: self-improving
description: "Triggered after task completion to review whether the just-finished work contains reusable knowledge, patterns, or corrections worth saving as a skill. Use this whenever you are asked to review a completed task for self-improvement. Decide whether to create a new skill, patch an existing one, promote an insight to CLAUDE.md, or decline."
---

# Self-Improving Skill

## When to Act

You have been prompted to review a recently completed task. Before doing anything, evaluate whether the task meets **at least one** of these thresholds:

1. **Non-trivial tooling** — The task required 3 or more tool calls (e.g., multiple Read/Edit/Bash/Grep interactions).
2. **Trial and error** — A command failed, a build broke, an assumption was wrong, and you had to retry or change course.
3. **New pattern discovered** — You found a gotcha, best practice, or workflow specific to this codebase or user's stack.
4. **User correction or preference** — The user said "No, that's wrong...", "Actually...", "I prefer...", "Always do...", or "Never do...".

If **none** apply, reply with exactly:

```
Nothing to save.
```

and stop. Do not read files, do not write files, do not explain your reasoning.

---

## Decision Tree

If you decide to act, choose **exactly one** of the following paths:

| Situation | Action |
|-----------|--------|
| Reusable workflow / command sequence / debugging procedure for this repo | Create or update a skill under `~/.claude/skills/` (or the current project's `.claude/skills/`) |
| Broadly applicable behavioral or architectural insight (cross-repo, cross-project) | Append or update `~/.claude/CLAUDE.md` |
| A skill already exists that covers this topic but is outdated or incomplete | Patch the existing skill's `SKILL.md` |

Prefer **patching an existing skill** over creating a new one. Check the current project's `.claude/skills/` and `~/.claude/skills/` directories before deciding to create.

---

## Skill Format

All skills must follow the Hermes-compatible format:

```yaml
---
name: {kebab-case-name}
description: "When to use this skill. Be specific and a little pushy so the skill triggers reliably."
metadata:
  tags: [tag1, tag2]
---

# {Title}

## Context

## Procedure

## Examples
```

### Naming Rules
- Match `^[a-z0-9][a-z0-9._-]*$` (lowercase, kebab-case preferred)
- Max 64 characters
- Be descriptive but concise (e.g., `go-build-troubleshooting`, `fastapi-testing-patterns`)

### Frontmatter Requirements
- `name` — must match the directory name
- `description` — include both *what* the skill does and *when* to use it
- `metadata.tags` — optional but helpful

### Content Limits
- `SKILL.md` body: keep it under 500 lines, under 100,000 characters
- Supporting files can go in `references/`, `templates/`, `scripts/`, `assets/`

### Storage Location Priority
1. Current project's `.claude/skills/<name>/SKILL.md` — for repo-specific learnings
2. `~/.claude/skills/<name>/SKILL.md` — for cross-project reusable skills

Use the current project's `.claude/skills/` when the learning is tightly coupled to this codebase.

---

## Patching an Existing Skill

If a skill already exists and needs a small update:

1. Read the current `SKILL.md`
2. Use `Edit` to apply the minimal change
3. Preserve the frontmatter

If the change is large (more than ~30% of the file), use `Write` to rewrite the full `SKILL.md` instead.

---

## Promoting to CLAUDE.md

If the insight is about:
- How this user wants you to behave (style, tone, workflow)
- Cross-project architectural principles
- Tool preferences that apply everywhere

Then append it to `~/.claude/CLAUDE.md` under an appropriate heading, or create a new heading if none fits.

---

## Action Protocol

When you decide to save something, you MUST use file tools. Do not just describe what you would do — do it.

### Creating a new skill
1. Pick the storage location (project-local `.claude/skills/` for repo-specific; `~/.claude/skills/` for reusable).
2. Use `Bash` to create the directory: `mkdir -p <path>/<name>`.
3. Use `Write` to create `<path>/<name>/SKILL.md` with valid frontmatter and body.
4. Reply with `Created skill: <name>`.

### Patching an existing skill
1. Use `Read` to read the current `SKILL.md`.
2. Use `Edit` to apply the minimal targeted change.
3. Reply with `Updated skill: <name>`.

### Promoting to CLAUDE.md
1. Use `Read` to check `~/.claude/CLAUDE.md` if it exists.
2. Use `Edit` or `Write` to append the insight under the right heading.
3. Reply with `Updated CLAUDE.md`.

---

## Response Format

After writing or editing, give the user a one-line summary:

- `Created skill: <name>`
- `Updated skill: <name>`
- `Updated CLAUDE.md`
- `Nothing to save.`