# hfro

A Claude Code plugin that adds two read-only reviewers to every session:

- **plan-advisor** checks the plan of a larger or riskier task before any work starts. It runs only when the plan has several steps, a real choice between approaches, ambiguous requirements, or a hard-to-undo step.
- **advisor** must approve every non-trivial result before it reaches you. It finds every change itself (`git status` / `git diff`, not only the files it is told about), checks each requirement, runs read-only checks, and returns APPROVE or REVISE with exact fixes. Hooks enforce this: a session that changed files cannot finish until the advisor has reviewed the latest version, and a REVISE verdict cannot be silently dropped (at most 4 rounds).

Both reviewers run on Fable (`model: fable`; the advisor at high effort, the plan-advisor at medium). When Fable is at its usage or rate limit or unavailable, the session calls the reviewer again on the fallback model, by default **Opus at high effort**, and keeps it for the rest of that task.

The workflow instructions are loaded into each session at start-up by a SessionStart hook, so nothing is written into your projects' CLAUDE.md.

## Install

In a terminal, once per computer:

```bash
claude plugin marketplace add hfrotan/claude-plugins
claude plugin install hfro@hfrotan-tools --scope user
```

Or inside a Claude Code session: `/plugin marketplace add hfrotan/claude-plugins`, then `/plugin install hfro@hfrotan-tools`. Start a new session (or run `/reload-plugins`) to load it.

### Moving from advisor-kit

This plugin used to be called `advisor-kit`. The marketplace maps the old name to the new one (`renames` in `marketplace.json`), so Claude Code moves your settings to `hfro`. Run the install line above once so the new name is cached. If `advisor-kit` is still listed in `/plugin` after that, remove it so the hooks do not run twice:

```bash
claude plugin uninstall advisor-kit@hfrotan-tools --scope user
```

## Update

```bash
claude plugin marketplace update hfrotan-tools
claude plugin update hfro@hfrotan-tools
```

To skip these commands, turn on auto-update for the marketplace in `/plugin` > Marketplaces.

Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` whenever you change the plugin.

## Settings

Claude Code asks for these when you install or enable the plugin inside a session (`claude plugin install` in a terminal does not ask; unset values use the defaults). To change them later, run this inside a session:

```
/plugin configure hfro@hfrotan-tools
```

New values apply from the next session.

| Setting | Values | Default | What it does |
|---|---|---|---|
| `fallback_model` | `opus`, `sonnet`, `haiku`, `off` | `opus` | Model the reviewers use when Fable is at its limit or unavailable. `off` means no fallback: the session tells you the review could not run. |
| `fallback_effort` | `low`, `medium`, `high`, `xhigh`, `max` | `high` | Effort level for the fallback model. |

Claude Code's own `fallbackModel` setting does not help here: it only covers overloaded or unavailable models, not usage or rate limits, and a plugin cannot set it.

## Use

- `/hfro:advisor [focus]` sends the latest result to the advisor now.
- `/hfro:noadvisor <task>`, or "noadvisor" anywhere in your message, skips both reviews for that task.

The final message ends with one line such as `Advisor: approved (round 2)`, `Advisor: approved (round 1, opus fallback)`, `Advisor: not approved after 4 rounds - see open issues`, `Advisor: paused - waiting for your answer`, `Advisor: not run - model limit` or `Advisor: skipped - changes not made by this task`. The gate reads these markers only on the last line.

## How the gate works

- **UserPromptSubmit** records a fingerprint of the git working tree (tracked and untracked files, `.gitignore` respected). This catches changes made by any means: edit tools, shell commands, MCP tools and subagents. Outside a git repository only the main session's Write, Edit, MultiEdit and NotebookEdit calls are seen.
- **PostToolUse** on the Agent tool records the fingerprint of the tree each advisor review saw.
- **Stop** blocks the session from finishing when files changed and the latest version was not reviewed, or the last verdict was REVISE and nothing was done about it. An advisor call that returned no verdict (a usage-limit error, or a call started in the background) does not count as a round; the block message then names the fallback model, or asks for a foreground call. When only the tree changed (no edit tool), the message lists the changed files, and the session can answer with `Advisor: skipped - changes not made by this task` if it did not make them. Files the session writes in the temp directory do not count. The gate gives up after being ignored twice in a row, so it can never loop.

The fingerprint is computed through a private index file in the temp directory, so it never touches your real git index or your files. It does write git objects into `.git/objects`, which `git gc` cleans up.

## Requirements

The hooks run with Node.js when it is installed and fall back to Python 3 (`advisor-gate.py`) when it is not. Hooks need a POSIX shell: on Windows that is Git Bash. Change detection beyond the edit tools needs `git`. Keep `advisor-gate.js` and `advisor-gate.py` in step: any change to one must be made in the other.
