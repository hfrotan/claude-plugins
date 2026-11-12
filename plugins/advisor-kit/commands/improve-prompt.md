---
description: Improve a draft prompt with the prompt-optimizer agent
argument-hint: <draft prompt>
---

Call the prompt-optimizer subagent now (Agent tool, subagent_type "advisor-kit:prompt-optimizer"). Send it:
- DRAFT: the draft below, word for word
- TARGET: where the prompt will be used, only if the user said so
- CONTEXT: only facts from this conversation that the draft depends on, such as file paths

If the draft below is empty, ask the user to paste the prompt they want improved, and stop.

Show the user the agent's answer as it is: the improved prompt in its code block, then the changes, the optional additions and the questions.

Do not carry out the improved prompt, and do not start the task it describes. The user decides what to send. This needs neither the plan review nor the advisor review.

Draft:
$ARGUMENTS
