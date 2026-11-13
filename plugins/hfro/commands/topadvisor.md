---
description: Use Fable at high effort for the reviews; with a task, do it, without one, review the latest result now
argument-hint: [optional: task]
---

topadvisor: every review in this task, the plan review and the result review, runs on Fable at high effort. Call the reviewers with `model: "fable"` and `effort: "high"` and name them "Fable ... High". If Fable is at its limit or unavailable, use `model: "opus"` and `effort: "high"`, then the fallback from the workflow. The next task goes back to normal.

- If the task below is empty: run the result review from the hfro workflow now on the latest result, even if it was trivial or skipped. Start at round 1 unless a review loop is running, and continue until APPROVE or round 4.
- Otherwise: do the task below with the normal hfro workflow (plan review first, result review at the end), using these reviewers.

Task: $ARGUMENTS
