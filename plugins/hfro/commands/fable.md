---
description: Review with Fable at medium effort; with a task, do it with a plan review and a result review, without one, review the latest result now
argument-hint: [optional: task]
---

Run the hfro review on Fable at medium effort. The reviewers are `hfro:fable-plan-advisor` (the plan) and `hfro:fable-advisor` (the result). Their model and effort are fixed in the agents, so pass no `model` or `effort` unless the fallback below says so.

- If the task below is empty: run the result review now on the latest result, even if it was trivial.
- Otherwise: do the task with a plan review before changing anything and a result review at the end.
- If the user answers a question you asked during a review, carry on with the same reviewer; the loop restarts at round 1.

The reviewer is there to find what you missed, so give it what it needs to check you, not a case for approval. Send the facts plainly, including what you skipped, could not test or are unsure about.

## Calling a reviewer

- Use the Agent tool with `run_in_background: false`. If a review runs in the background anyway, do not start another one: end your turn with the running line alone and continue when the verdict arrives.
- Until the last review approves (or the loop ends), write no result, summary or draft answer in any message, including between rounds.
- Set `description` to "Plan review by <reviewer>" or "Result review by <reviewer>", where <reviewer> is the model and effort that run, for example "Fable Medium".
- If a call returns no verdict because the model is at its usage or rate limit, overloaded or unavailable, switch reviewer and keep the new one for the rest of the task: first call `hfro:opus-plan-advisor` or `hfro:opus-advisor` instead (the reviewer is then "Opus High"); if that fails too, call that Opus agent with `model: "sonnet"` ("Sonnet High"). A failed call is not a round. If no model is left, tell the user and end with "Review not run: model limit".

## Plan review (a task was given)

Write the plan first, including how you will check the result. Then send: ORIGINAL REQUEST (word for word, plus any later instruction or answer from the user that changed it), PLAN, ACCEPTANCE CRITERIA (short, checkable), CONTEXT (file paths, facts, assumptions), ROUND (1 or 2), and on round 2 PREVIOUS ISSUES with "changed: how" or "disagree: reason".

- PROCEED: apply any criteria changes and start.
- REVISE: fix the plan yourself. Call round 2 only for a blocking issue. Max 2 rounds.
- ASK USER: ask that question before starting.
- If the plan proves wrong mid-task, call it once more with WHAT CHANGED.

## Result review

Send: ORIGINAL REQUEST (word for word, plus any later instruction or answer from the user that changed it), WORK DONE, FILES (every file created or changed), PRE-EXISTING CHANGES (files that already had uncommitted changes before the task, or "none"), RESULT (the draft answer with every claim you will make), ACCEPTANCE CRITERIA (only if the plan was reviewed), ROUND (1-4), and from round 2 PREVIOUS ISSUES with "fixed: how" or "disagree: reason". Do not send the plan.

- APPROVE: finish.
- REVISE: fix every issue, or give your reason for disagreeing, and call the next round. Do not ignore an issue silently. Max 4 rounds; after round 4 list the open issues.
- If an issue needs the user's answer, ask the question and end with the paused line.
- If the last verdict lists anything under NOT VERIFIED, tell the user in the final message.

## Final message

Write it only once, after the last review, in plain English and as short as possible: what you did, what the user must do, what was not verified, and any open issues. Do not show review rounds. End with exactly one of these lines, naming the reviewer that actually ran:
- `Reviewed by <reviewer>: approved (round N)`
- `Plan review by <reviewer> · Reviewed by <reviewer>: approved (round N)`
- `Reviewed by <reviewer>: not approved after 4 rounds`
- `Review running: I'll show the result when it's done`
- `Review paused: waiting for your answer`
- `Review not run: model limit`

Task: $ARGUMENTS
