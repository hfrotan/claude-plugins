---
description: Ask the advisor (Fable at medium effort) for a second opinion now; with a problem or question, ask about that, without one, about whatever you are stuck on in this conversation
argument-hint: [optional: problem or question]
---

Get a second opinion from `hfro:fable-advisor` (Fable at medium effort) now. The model and effort are fixed in the agent, so pass no `model` or `effort` unless the fallback below says so. A project agent named `fable-advisor` in `.claude/agents/` replaces the plugin one: then drop the `hfro:` prefix.

- If the text below is empty: ask about the problem you are working on or stuck on in this conversation. If there is none, ask the user what they want advice on and stop.
- Otherwise: ask about that problem or question.

The advisor is there to find what you got wrong, so do not argue for your theory. Send the facts plainly, including what you are unsure about.

## The call

Call the Agent tool with `run_in_background: false` and `description` "Advice from <advisor>", where <advisor> is the model and effort that run, for example "Fable Medium". Send:

- GOAL: what the user wants, in their words
- WHAT I TRIED: each attempt and its exact result, or "nothing yet"
- ERROR: the full output, not a paraphrase, or "none"
- FILES: the paths involved
- MY THEORY: your current guess, or "none"
- CONSTRAINTS: anything the user ruled out or required

If a call returns no answer because the model is at its usage or rate limit, overloaded or unavailable, call the same agent again with `model: "sonnet"` (the advisor is then "Sonnet Medium"). If no model is left, tell the user and stop.

## After the advice

- If ASK THE USER is not "nothing", ask that question and wait.
- If the user only asked for advice, give it to them in plain English: the diagnosis, how sure the advisor is, and the next step it recommends.
- If you were in the middle of a task, do the NEXT STEP, then IF THAT FAILS if needed, and drop what STOP DOING names. Do not take a step that destroys work or affects anything outside the project without asking the user first.

End your reply with one line naming the advisor that actually ran: `Advice from <advisor>`.

Problem: $ARGUMENTS
