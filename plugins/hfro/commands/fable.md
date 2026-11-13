---
description: Review with Fable at medium effort; with a task, do it with a plan review and a result review, without one, review the latest result now
argument-hint: [optional: task]
---

hfro:fable: run the hfro review workflow with both reviewers on Fable at medium effort (`model: "fable"`, `effort: "medium"`).

- If the task below is empty: run the result review now on the latest result, even if it was trivial. Start at round 1 unless a review loop is running, and continue until APPROVE or round 4.
- Otherwise: do the task below with a plan review before changing anything and a result review at the end.

Task: $ARGUMENTS
