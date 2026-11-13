# hfro

A Claude Code plugin with two read-only reviewers that run only when you ask:

- **plan-advisor** checks the plan of a task before work starts.
- **advisor** checks the result. It finds all changes itself with git, runs checks, and returns APPROVE or REVISE. Hooks stop the session from finishing until the latest change is approved, for at most 4 rounds.

Nothing runs automatically, so normal tasks cost no extra time or tokens. You see only the final answer, after the review is done. The plugin asks for the reviews to run in the foreground; if Claude Code still runs one in the background, you see one short line, `Review running: I'll show the result when it's done`, and then the result.

If the reviewer is at its limit, Fable switches to Opus at high effort, and Opus switches to the fallback model, Sonnet at high effort by default. Each reviewed answer ends with one short line naming the reviewer that actually ran, for example:

```
Plan review by Opus 5.5 High · Reviewed by Opus 5.5 High: approved (round 2)
```

## Install

```bash
claude plugin marketplace add hfrotan/claude-plugins
```
```bash
claude plugin install hfro@hfrotan-tools --scope user
```

If you used the old `advisor-kit` plugin, remove it so the hooks do not run twice:

```bash
claude plugin uninstall advisor-kit@hfrotan-tools --scope user
```

## Update

```bash
claude plugin marketplace update hfrotan-tools
```
```bash
claude plugin update hfro@hfrotan-tools
```

## Settings

Change them inside a session with `/plugin configure hfro@hfrotan-tools`. They apply from the next session.

| Setting | Values | Default |
|---|---|---|
| `fallback_model` | `fable`, `opus`, `sonnet`, `haiku`, `off` | `sonnet` |
| `fallback_effort` | `low`, `medium`, `high`, `xhigh`, `max` | `high` |

## Use

- `/hfro:opus` reviews the latest result now on Opus at high effort.
- `/hfro:opus <task>` does the task with a plan review first and a result review at the end, both on Opus at high effort.
- `/hfro:fable` and `/hfro:fable <task>` do the same on Fable at medium effort.

## Notes

- Change detection hashes the git working tree through a private index in the temp folder. It never touches your files or your git index. Outside git, only the edit tools are seen.
- The hooks run with Node.js, or Python 3 when Node is missing. Keep `advisor-gate.js` and `advisor-gate.py` in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
