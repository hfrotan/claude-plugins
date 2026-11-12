# hfro review workflow

Two read-only reviewers check your work: `hfro:plan-advisor` checks a plan before you start, `hfro:advisor` checks the result before you finish. A project agent named `advisor` or `plan-advisor` in `.claude/agents/` replaces the plugin one: then drop the `hfro:` prefix.

## Skip

Skip both for questions, explanations, reviews of existing work, chat and lookups, and when the user's message contains "noadvisor" or "no advisor" (that task only).

## Calling a reviewer

- Use the Agent tool with `run_in_background: false`, and set `description` to "Plan review by <model> <effort>" or "Result review by <model> <effort>", for example "Result review by Fable 5.2 Medium". Use the model's full name and version.
- Both run on Fable at medium effort by default.
- After each review a hook tells you the model and effort that actually ran, for example "Fable 5.2 Medium". Use that name in the final line.
<!-- fallback:on -->
- If a call returns no verdict because Fable is at its usage or rate limit, overloaded or unavailable, call it again with `model: "{{FALLBACK_MODEL}}"` and `effort: "{{FALLBACK_EFFORT}}"` (the user set this in the plugin settings) and keep that for the rest of the task. A failed call is not a round. If the fallback fails too, tell the user.
<!-- /fallback:on -->
<!-- fallback:off -->
- If a call returns no verdict because Fable is at its limit or unavailable, do not retry with another model: tell the user.
<!-- /fallback:off -->

## Plan review

Call the plan-advisor before changing anything when the task needs a result review and the plan has more than about three steps, a real choice between approaches, an ambiguity that changes what you would build, or a hard-to-undo step (deleting data, a migration, sending or publishing). Also when the user asks.

Send: ORIGINAL REQUEST (word for word), PLAN, ACCEPTANCE CRITERIA (short, checkable), CONTEXT (file paths, facts, assumptions), ROUND (1 or 2), and on round 2 PREVIOUS ISSUES with "changed: how" or "disagree: reason".

- PROCEED: apply any criteria changes and start.
- REVISE: fix the plan yourself. Call round 2 only for a blocking issue. Max 2 rounds.
- ASK USER: ask that question before starting.
- If the plan proves wrong mid-task, call it once more with WHAT CHANGED.

## Result review

Call the advisor after the work if the task changed any file (by any tool, shell command, MCP tool or subagent) or produced a deliverable, and whenever the user asks. A plan review never replaces it.

Send: ORIGINAL REQUEST (word for word), WORK DONE, FILES (every file changed), PRE-EXISTING CHANGES (files that already had uncommitted changes before the task, or "none"), RESULT (the draft answer with every claim you will make), ACCEPTANCE CRITERIA (only if the plan was reviewed), ROUND (1-4), and from round 2 PREVIOUS ISSUES with "fixed: how" or "disagree: reason". Do not send the plan.

- APPROVE: finish.
- REVISE: fix every issue, or give your reason for disagreeing, and call the next round. Max 4 rounds; after round 4 list the open issues.
- If an issue needs the user's answer, ask and end with the paused line. The loop restarts at round 1 after they answer.

A Stop hook enforces this and tells you what is missing. If it lists changed files this task did not make and you used no edit tool, end with the skipped line.

## Final message

Write it in plain English and as short as possible: what you did, what the user must do, and any open issues. No jargon, headings or tables unless the user asks. Do not show review rounds.

End with exactly one of these as the last line (the hook reads only the last line):
- `Reviewed by <reviewer>: approved (round N)`
- `Plan review by <reviewer> · Reviewed by <reviewer>: approved (round N)`
- `Reviewed by <reviewer>: not approved after 4 rounds`
- `Review paused: waiting for your answer`
- `Review not run: model limit`
- `Review skipped: changes not made by this task`
- nothing, when the reviews were skipped

<reviewer> is the model and effort the hook reported, for example "Fable 5.2 Medium" or "Opus 5.5 High".
