---
description: Review with Fable at medium effort; with a task, do it with a plan review and a result review, without one, review the latest result now
argument-hint: [optional: task]
---

Run the hfro review with both reviewers on Fable at medium effort: pass `model: "fable"` and `effort: "medium"` to every reviewer call.

- If the task below is empty: run the result review now on the latest result, even if it was trivial.
- Otherwise: do the task with a plan review before changing anything and a result review at the end.
- If the user answers a question you asked during a review, carry on with the same reviewer; the loop restarts at round 1.

## Calling a reviewer

- `hfro:plan-advisor` reviews the plan, `hfro:advisor` the result. A project agent named `plan-advisor` or `advisor` in `.claude/agents/` replaces the plugin one: then drop the `hfro:` prefix.
- Use the Agent tool with `run_in_background: false`. If a review runs in the background anyway, do not start another one: end your turn with the running line alone and continue when the verdict arrives.
- Until the last review approves (or the loop ends), write no result, summary or draft answer in any message, including between rounds.
- Set `description` to "Plan review by <reviewer>" or "Result review by <reviewer>", where <reviewer> is the model's full name and version plus the effort, for example "Fable 5.2 Medium".
- If a call returns no verdict because the model is at its usage or rate limit, overloaded or unavailable, call it again with the next model and keep it for the rest of the task: Fable is followed by `model: "opus"`, `effort: "high"`, then `model: "sonnet"`, `effort: "high"`. A failed call is not a round. If no model is left, tell the user and end with "Review not run: model limit".

## Plan review (a task was given)

Send: ORIGINAL REQUEST (word for word), PLAN, ACCEPTANCE CRITERIA (short, checkable), CONTEXT (file paths, facts, assumptions), ROUND (1 or 2), and on round 2 PREVIOUS ISSUES with "changed: how" or "disagree: reason".

- PROCEED: apply any criteria changes and start.
- REVISE: fix the plan yourself. Call round 2 only for a blocking issue. Max 2 rounds.
- ASK USER: ask that question before starting.
- If the plan proves wrong mid-task, call it once more with WHAT CHANGED.

## Result review

Send: ORIGINAL REQUEST (word for word), WORK DONE, FILES (every file changed), PRE-EXISTING CHANGES (files that already had uncommitted changes before the task, or "none"), RESULT (the draft answer with every claim you will make), ACCEPTANCE CRITERIA (only if the plan was reviewed), ROUND (1-4), and from round 2 PREVIOUS ISSUES with "fixed: how" or "disagree: reason". Do not send the plan.

- APPROVE: finish.
- REVISE: fix every issue, or give your reason for disagreeing, and call the next round. Max 4 rounds; after round 4 list the open issues.
- If an issue needs the user's answer, ask the question and end with the paused line.

## Final message

Write it only once, after the last review, in plain English and as short as possible: what you did, what the user must do, and any open issues. Do not show review rounds. End with exactly one of these lines, naming the reviewer that actually ran:
- `Reviewed by <reviewer>: approved (round N)`
- `Plan review by <reviewer> · Reviewed by <reviewer>: approved (round N)`
- `Reviewed by <reviewer>: not approved after 4 rounds`
- `Review running: I'll show the result when it's done`
- `Review paused: waiting for your answer`
- `Review not run: model limit`

Task: $ARGUMENTS
