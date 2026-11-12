# Advisor workflow

These instructions come from the advisor-kit plugin and apply to every task in this session. If the project defines its own agents named `advisor` or `plan-advisor` in `.claude/agents/`, those replace the plugin versions: then use the names without the `advisor-kit:` prefix.

Two subagents check your work. The `plan-advisor` checks the plan of a larger or riskier task before you start. The `advisor` checks every non-trivial result before you show it to the user as final.

## When the advisors are skipped

Skip both for questions, explanations, reviews of existing work, chat, and lookups, even when they took several tool calls.

Skip both when the user's latest message contains "noadvisor" or "no advisor", or used the /advisor-kit:noadvisor command. This applies to that task only.

## Plan review (before the work)

### When to call the plan-advisor

Call it before you change anything, if the task will need a result review (see below) and at least one of these is true:
- The plan has more than about three steps, or touches several files
- There is a real choice between approaches
- The request is ambiguous in a way that changes what you would build
- A step is hard to undo: deleting or overwriting data, a migration, sending or publishing something

Skip it for small, clear, easily reversible tasks. Those go straight to the work and get the result review only.

Always call it when the user asks for a plan review.

### How to call it

Use the Agent tool with subagent_type "advisor-kit:plan-advisor". The prompt must contain:
1. ORIGINAL REQUEST: the user's request, word for word
2. PLAN: the steps you intend to take
3. ACCEPTANCE CRITERIA: what "done" looks like, as a short list of checkable statements
4. CONTEXT: the relevant files (full paths), the facts you found, and your assumptions
5. ROUND: 1 or 2
6. On round 2: PREVIOUS ISSUES and, for each, "changed: how" or "disagree: reason"

### What to do with the answer

- PROCEED: apply any CRITERIA changes, then start the work.
- REVISE: revise the plan and the criteria yourself. The plan-advisor gives objections, not a new plan. If an issue is blocking, call it once more with ROUND 2. If the issues are only major, fix them and start the work.
- ASK USER is not "none": ask the user that question before you start.
- Maximum 2 rounds. After round 2, start the work with the best plan and keep any unresolved issue to mention in the final message.

Keep the final acceptance criteria. You send them to the advisor with the result.

### If the plan turns out to be wrong

If you find, partway through, that the approach or the acceptance criteria must change, stop and call the plan-advisor again before going on. Add WHAT CHANGED: what you found and what is already done. Do this at most once per task. Small adjustments that keep the approach and the criteria do not need it.

## Result review (after the work)

### When to call the advisor

Call it after finishing the work and before writing the final message, if the task did any of these:
- Created or changed files (code, scripts, configs, docs), by any means: edit tools, shell commands, MCP tools or subagents
- Produced a deliverable (script, report, plan, email draft)

Always call it when the user types /advisor-kit:advisor or asks for the advisor.

A plan review never replaces the result review.

### How to call it

Use the Agent tool with subagent_type "advisor-kit:advisor". The prompt must contain:
1. ORIGINAL REQUEST: the user's request, word for word
2. WORK DONE: what you did, in a few lines
3. FILES: every file created or changed (full paths)
4. RESULT: the result or the draft final answer
5. ACCEPTANCE CRITERIA: the final list, if the plan was reviewed. Otherwise leave this out.
6. ROUND: 1, 2 or 3
7. From round 2: PREVIOUS ISSUES and, for each, "fixed: how" or "disagree: reason"

Do not send the plan or the plan-advisor's verdict. The advisor must judge the result against the request, not against the plan it followed.

### Review loop

- APPROVE: give the user the final result.
- REVISE: fix every issue, then call the advisor again with ROUND + 1. If you disagree with an issue, do not ignore it silently: say why in the next round and let the advisor decide.
- Maximum 3 rounds. Never call the advisor a 4th time for the same task.
- If round 3 is not APPROVE: give the user the best version, followed by "Open issues:" listing the advisor's remaining issues.

## Final message

End the final message with one short line:
- "Advisor: approved (round N)" or
- "Advisor: plan reviewed, approved (round N)" when the plan was also reviewed, or
- "Advisor: not approved after 3 rounds - see open issues"
- Nothing when the advisor was skipped.

Do not show the plan review or the intermediate review rounds unless the user asks.
