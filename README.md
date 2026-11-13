# hfro

A Claude Code plugin with a read-only reviewer, the **advisor**, that runs only when you ask. Claude does the task, the advisor checks the result, Claude fixes what it found and asks again, for at most 4 rounds. Then you get the answer.

The advisor does not rely on Claude's own account of the work. It finds all changes itself with git, runs the tests and the changed behaviour, checks every claim in the draft answer, and returns APPROVE or REVISE. Every issue comes with evidence and an exact fix. Anything it could not verify is passed on to you.

It comes in two strengths, fixed in the agent files: `/hfro:opus` always reviews on Opus at high effort and `/hfro:fable` on Fable at medium effort.

Nothing runs automatically and the plugin has no hooks, so normal tasks cost no extra time or tokens. The review instructions load only when you use a command. You see only the final answer, after the review is done; if Claude Code runs a review in the background, you see one short line, `Review running: I'll show the result when it's done`, and then the result.

If the reviewer is at its limit, Fable switches to Opus at high effort, and Opus switches to Sonnet at high effort. Each reviewed answer ends with one short line naming the reviewer that actually ran, for example:

```
Reviewed by Opus High: approved (round 2)
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
- `/hfro:opus <task>` does the task and has the result reviewed on Opus at high effort before you see it.
- `/hfro:fable` and `/hfro:fable <task>` do the same on Fable at medium effort.

## Notes

- The review steps live in `plugins/hfro/commands/opus.md` and `fable.md`; keep the two in step.
- The reviewer lives in `plugins/hfro/agents/`. `opus-advisor.md` and `fable-advisor.md` differ only in name, description, model and effort; keep the rest in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
