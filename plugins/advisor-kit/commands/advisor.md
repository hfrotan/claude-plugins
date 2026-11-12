---
description: Send the latest result to the advisor for review now
argument-hint: [optional: what to focus on]
---

Call the advisor subagent now on the most recent result in this conversation, even if the task was trivial or was skipped earlier.

Follow the result review in the Advisor workflow (loaded at session start by the advisor-kit plugin) exactly: send the user's original request word for word, what was done, the files changed, the result, the acceptance criteria if the plan was reviewed, and the round number (start at round 1 unless a review loop is already running). Then run the loop until APPROVE or round 3.

Extra focus from the user (may be empty): $ARGUMENTS
