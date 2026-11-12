# advisor-kit

A Claude Code plugin that adds two read-only reviewers to every session:

- **plan-advisor** checks the plan of a larger or riskier task before any work starts. It runs only when the plan has several steps, a real choice between approaches, ambiguous requirements, or a hard-to-undo step.
- **advisor** must approve every non-trivial result before it reaches you. A Stop hook enforces this: a session that changed files cannot finish until the advisor has reviewed the latest version (at most 4 rounds).

The workflow instructions are loaded into each session at start-up by a SessionStart hook, so nothing is written into your projects' CLAUDE.md.

## Install

In a terminal, once per computer:

```bash
claude plugin marketplace add hfrotan/claude-plugins
claude plugin install advisor-kit@hfrotan-tools --scope user
```

Or inside a Claude Code session: `/plugin marketplace add hfrotan/claude-plugins`, then `/plugin install advisor-kit@hfrotan-tools`. Start a new session (or run `/reload-plugins`) to load it.

## Update

```bash
claude plugin marketplace update hfrotan-tools
claude plugin update advisor-kit@hfrotan-tools
```

To skip these commands, turn on auto-update for the marketplace in `/plugin` > Marketplaces.

Bump `version` in `plugins/advisor-kit/.claude-plugin/plugin.json` whenever you change the plugin.

## Use

- `/advisor-kit:advisor [focus]` sends the latest result to the advisor now.
- `/advisor-kit:noadvisor <task>`, or "noadvisor" anywhere in your message, skips both reviews for that task.

## Requirements

The Stop hook runs with Node.js when it is installed and falls back to Python 3 (`advisor-gate.py`) when it is not. Hooks need a POSIX shell: on Windows that is Git Bash. Keep `advisor-gate.js` and `advisor-gate.py` in step: any change to one must be made in the other.

Both agents use `model: fable` (the newest Fable model your account can use) at medium effort.
