# hfro

A Claude Code plugin with read-only reviewers that run only when you ask:

- The **plan-advisor** checks the plan of a task before work starts: it tests the plan's assumptions against the real files, looks for the likeliest way it fails, and checks that the plan says how the result will be verified.
- The **advisor** checks the result. It does not rely on Claude's own account of the work: it finds all changes itself with git, runs the tests and the changed behaviour, checks every claim in the draft answer, and returns APPROVE or REVISE. Every issue comes with evidence and an exact fix. Claude fixes the issues and asks again, for at most 4 rounds. Anything the reviewer could not verify is passed on to you.

Each has an Opus version (high effort) and a Fable version (medium effort). The model and effort are fixed in the agent files, so `/hfro:opus` always reviews on Opus at high effort and `/hfro:fable` on Fable at medium effort.

Nothing runs automatically and the plugin has no hooks, so normal tasks cost no extra time or tokens. The review instructions load only when you use a command. You see only the final answer, after the review is done; if Claude Code runs a review in the background, you see one short line, `Review running: I'll show the result when it's done`, and then the result.

If the reviewer is at its limit, Fable switches to Opus at high effort, and Opus switches to Sonnet at high effort. Each reviewed answer ends with one short line naming the reviewer that actually ran, for example:

```
Plan review by Opus High · Reviewed by Opus High: approved (round 2)
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
- The reviewers live in `plugins/hfro/agents/`. The `opus-*` and `fable-*` files differ only in name, description, model and effort; keep the rest in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
