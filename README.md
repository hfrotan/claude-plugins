# hfro

A Claude Code plugin with two read-only reviewers:

- **plan-advisor** checks the plan of a larger or riskier task before work starts (Fable, medium effort).
- **advisor** checks every change before you see the result (Fable, medium effort). It finds all changes itself with git, runs checks, and returns APPROVE or REVISE. Hooks stop the session from finishing until the latest change is approved, for at most 4 rounds.

If Fable is at its limit, the reviewers switch to the fallback model, Opus at high effort by default. Each answer ends with one short line naming the reviewer that actually ran, for example:

```
Plan review by Fable 5.2 Medium · Reviewed by Opus 5.5 High: approved (round 2)
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
| `fallback_model` | `opus`, `sonnet`, `haiku`, `off` | `opus` |
| `fallback_effort` | `low`, `medium`, `high`, `xhigh`, `max` | `high` |

## Use

- `/hfro:advisor [focus]` reviews the latest result now.
- `/hfro:noadvisor <task>`, or "noadvisor" in your message, skips both reviews for that task.

## Notes

- Change detection hashes the git working tree through a private index in the temp folder. It never touches your files or your git index. Outside git, only the edit tools are seen.
- The hooks run with Node.js, or Python 3 when Node is missing. Keep `advisor-gate.js` and `advisor-gate.py` in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
