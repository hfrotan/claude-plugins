# hfro

A Claude Code plugin with a read-only **advisor**: a senior second opinion for when Claude is stuck. It never edits files. It finds the root cause itself and tells Claude the single best next move.

Claude calls it on its own, without being asked, when:

- the same error or test failure survives 2 fix attempts
- it is going in circles or undoing its own changes
- it cannot explain why something fails
- a command, build, install or tool keeps failing for reasons it does not understand
- it has to choose between approaches and the choice is hard to reverse
- it is about to do something risky to get unstuck (deleting data, force-pushing, disabling tests, broad rewrites)
- you ask for advice or a second opinion

It does not run for routine work that is going fine. The advisor does not trust Claude's theory: it reproduces the failure, checks the usual blind spots (wrong file, stale build, wrong version, a downstream error), and answers with a diagnosis, its confidence, the evidence, one concrete next step, what to check if that fails, what to stop doing, and any question only you can answer.

There are two advisors, with model and effort fixed in the agent files. `opus-advisor` (Opus at high effort) is the one Claude uses first. `fable-advisor` (Fable at medium effort) takes over when Opus's advice did not solve the problem or Opus is at its limit. If Fable is at its limit too, Sonnet at medium effort is used.

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

The advisor works on its own. To ask it directly:

- `/hfro:opus` asks Opus at high effort about whatever Claude is stuck on right now.
- `/hfro:opus <problem or question>` asks about that.
- `/hfro:fable` and `/hfro:fable <problem or question>` do the same on Fable at medium effort.

The answer ends with one line naming the advisor that actually ran, for example `Advice from Opus High`.

## Notes

- The command steps live in `plugins/hfro/commands/opus.md` and `fable.md`; keep the two in step.
- The advisor lives in `plugins/hfro/agents/`. `opus-advisor.md` and `fable-advisor.md` differ only in name, description, model and effort; keep the rest in step.
- Bump `version` in `plugins/hfro/.claude-plugin/plugin.json` on every change.
