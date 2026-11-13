---
description: Review with Fable at medium effort; with a task, do it and have the result reviewed before you see it, without one, review the latest result now
argument-hint: [optional: task]
---

Have your work reviewed by `hfro:fable-advisor` (Fable at medium effort) before the user sees it. The model and effort are fixed in the agent, so pass no `model` or `effort` unless the fallback below says so. A project agent named `fable-advisor` in `.claude/agents/` replaces the plugin one: then drop the `hfro:` prefix.

- If the task below is empty: review the latest result now, even if it was trivial.
- Otherwise: do the task, then have the result reviewed.

The reviewer is there to find what you missed, so do not argue for approval. Send the facts plainly, including what you skipped, could not test or are unsure about.

## The review

Call the Agent tool with `run_in_background: false` and `description` "Result review by <reviewer>", where <reviewer> is the model and effort that run, for example "Fable Medium". Send:

- ORIGINAL REQUEST: word for word, plus any later instruction or answer from the user that changed it
- WORK DONE
- FILES: every file created or changed
- PRE-EXISTING CHANGES: files that already had uncommitted changes before the task, or "none"
- RESULT: the draft answer with every claim you will make
- ROUND: 1-4, and from round 2 PREVIOUS ISSUES with "fixed: how" or "disagree: reason"

Then:

- APPROVE: finish.
- REVISE: fix every issue, or give your reason for disagreeing, and call the next round. Never ignore an issue silently. Max 4 rounds; after round 4 list the open issues.
- If an issue needs the user's answer, ask the question and end with the paused line. The loop restarts at round 1 after they answer.
- Until the review ends, write no result, summary or draft answer in any message, including between rounds.
- If a review runs in the background anyway, do not start another one: end your turn with the running line alone and continue when the verdict arrives.
- If a call returns no verdict because the model is at its usage or rate limit, overloaded or unavailable, switch reviewer and keep the new one for the rest of the task: first call `hfro:opus-advisor` instead (the reviewer is then "Opus High"); if that fails too, call it with `model: "sonnet"` ("Sonnet High"). A failed call is not a round. If no model is left, tell the user and end with "Review not run: model limit".

## Final message

Write it only once, after the last review, in plain English and as short as possible: what you did, what the user must do, anything the reviewer listed under NOT VERIFIED, and any open issues. Do not show review rounds. End with exactly one of these lines, naming the reviewer that actually ran:
- `Reviewed by <reviewer>: approved (round N)`
- `Reviewed by <reviewer>: not approved after 4 rounds`
- `Review running: I'll show the result when it's done`
- `Review paused: waiting for your answer`
- `Review not run: model limit`

Task: $ARGUMENTS
