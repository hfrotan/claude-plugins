# hfro

A Claude Code plugin with two read-only reviewers:

- **plan-advisor** checks the plan of every task that changes files, before work starts (Opus, high effort). A hook blocks the edit tools until a plan review has run; a follow-up such as "yes" keeps the current plan.
- **advisor** checks every change before you see the result (Opus, high effort). It finds all changes itself with git, runs checks, and returns APPROVE or REVISE. Hooks stop the session from finishing until the latest change is approved, for at most 4 rounds.

You see only the final answer, after the review is done. The plugin asks for the reviews to run in the foreground; if Claude Code still runs one in the background, you see one short line, `Review running: I'll show the result when it's done`, and then the result.

`/hfro:topadvisor` runs both reviews on Fable at high effort instead. If the reviewer is at its limit, Fable switches to Opus at high effort, and Opus switches to the fallback model, Sonnet at high effort by default. Each answer ends with one short line naming the reviewer that actually ran, for example:

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

- `/hfro:advisor [focus]` reviews the latest result now.
- `/hfro:topadvisor` reviews the latest result now on Fable at high effort; `/hfro:topadvisor <task>` does the task with both reviews on Fable at high effort.
- `/hfro:noadvisor <task>`, or "noadvisor" in your message, skips both reviews for that task.

## Notes

- Change detection hashes the git working tree through a private index in the temp folder. It never touches your files or your git index. Outside git, only the edit tools are seen. The plan gate covers the edit tools, not changes made with shell commands.
- The hooks run with Node.js, or Python 3 when Node is missing. Keep `advisor-gate.js` and `advisor-gate.py` in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
