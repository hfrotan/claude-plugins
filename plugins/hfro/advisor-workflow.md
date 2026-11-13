# hfro review workflow (on demand)

Two read-only reviewers: `hfro:plan-advisor` checks a plan before you start, `hfro:advisor` checks the result. They run only when the user types `/hfro:opus` or `/hfro:fable`. Otherwise never call them and add no review line. A project agent named `advisor` or `plan-advisor` in `.claude/agents/` replaces the plugin one: then drop the `hfro:` prefix.

## The commands

- `/hfro:opus`: both reviewers on `model: "opus"`, `effort: "high"`.
- `/hfro:fable`: both reviewers on `model: "fable"`, `effort: "medium"`.
- With no task after the command: run the result review now on the latest result.
- With a task: do it, with a plan review before changing anything and a result review at the end.
- If the user answers a question you asked during a review, carry on with the same reviewer: the loop restarts at round 1.

## Calling a reviewer

- Use the Agent tool with `run_in_background: false` and always pass the `model` and `effort` of the command. A hook also asks for the foreground.
- The user wants only the final answer. Until the last review approves (or the loop ends), write no result, summary or draft answer in any message, including between rounds.
- If a review runs in the background anyway, do not start another one: end your turn with the running line alone, nothing else, and write the full answer when the verdict arrives.
- Set `description` to "Plan review by <model> <effort>" or "Result review by <model> <effort>", for example "Result review by Opus 5.5 High". Use the model's full name and version.
- After each review a hook tells you the model and effort that actually ran. Use that name in the final line.
- If a Fable review returns no verdict because Fable is at its limit or unavailable, call it again with `model: "opus"` and `effort: "high"`.
<!-- fallback:on -->
- If an Opus review returns no verdict because Opus is at its usage or rate limit, overloaded or unavailable, call it again with `model: "{{FALLBACK_MODEL}}"` and `effort: "{{FALLBACK_EFFORT}}"` (the user set this in the plugin settings) and keep that for the rest of the task. A failed call is not a round. If no model is left, tell the user.
<!-- /fallback:on -->
<!-- fallback:off -->
- If an Opus review returns no verdict because Opus is at its limit or unavailable, do not retry with another model: tell the user. A failed call is not a round.
<!-- /fallback:off -->

## Plan review (a command with a task)

Send: ORIGINAL REQUEST (word for word), PLAN, ACCEPTANCE CRITERIA (short, checkable), CONTEXT (file paths, facts, assumptions), ROUND (1 or 2), and on round 2 PREVIOUS ISSUES with "changed: how" or "disagree: reason".

- PROCEED: apply any criteria changes and start.
- REVISE: fix the plan yourself. Call round 2 only for a blocking issue. Max 2 rounds.
- ASK USER: ask that question before starting.
- If the plan proves wrong mid-task, call it once more with WHAT CHANGED.

## Result review

Send: ORIGINAL REQUEST (word for word), WORK DONE, FILES (every file changed), PRE-EXISTING CHANGES (files that already had uncommitted changes before the task, or "none"), RESULT (the draft answer with every claim you will make), ACCEPTANCE CRITERIA (only if the plan was reviewed), ROUND (1-4), and from round 2 PREVIOUS ISSUES with "fixed: how" or "disagree: reason". Do not send the plan.

- APPROVE: finish.
- REVISE: fix every issue, or give your reason for disagreeing, and call the next round. Max 4 rounds; after round 4 list the open issues.
- If an issue needs the user's answer, ask the question and end with the paused line. Use the paused line only when your message asks the user a question.

After a command, a Stop hook enforces this and tells you what is missing. If it lists changed files this task did not make and you used no edit tool, end with the skipped line.

## Final message (after a command)

Write it only once, after the last review. Write it in plain English and as short as possible: what you did, what the user must do, and any open issues. No jargon, headings or tables unless the user asks. Do not show review rounds.

End with exactly one of these as the last line (the hook reads only the last line):
- `Reviewed by <reviewer>: approved (round N)`
- `Plan review by <reviewer> · Reviewed by <reviewer>: approved (round N)`
- `Reviewed by <reviewer>: not approved after 4 rounds`
- `Review running: I'll show the result when it's done`
- `Review paused: waiting for your answer`
- `Review not run: model limit`
- `Review skipped: changes not made by this task`

<reviewer> is the model and effort the hook reported, for example "Opus 5.5 High" or "Fable 5.2 Medium".
