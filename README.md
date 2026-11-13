# hfro

A Claude Code plugin with two read-only reviewers that run only when you ask:

- **plan-advisor** checks the plan of a task before work starts.
- **advisor** checks the result. It finds all changes itself with git, runs checks, and returns APPROVE or REVISE. Claude fixes the issues and asks again, for at most 4 rounds.

Nothing runs automatically and the plugin has no hooks, so normal tasks cost no extra time or tokens. The review instructions load only when you use a command. You see only the final answer, after the review is done; if Claude Code runs a review in the background, you see one short line, `Review running: I'll show the result when it's done`, and then the result.

If the reviewer is at its limit, Fable switches to Opus at high effort, and Opus switches to Sonnet at high effort. Each reviewed answer ends with one short line naming the reviewer that actually ran, for example:

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

If you used the old `advisor-kit` plugin, remove it:

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

## Use

- `/hfro:opus` reviews the latest result now on Opus at high effort.
- `/hfro:opus <task>` does the task with a plan review first and a result review at the end, both on Opus at high effort.
- `/hfro:fable` and `/hfro:fable <task>` do the same on Fable at medium effort.

## Notes

- The review steps live in `plugins/hfro/commands/opus.md` and `fable.md`; keep the two in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
