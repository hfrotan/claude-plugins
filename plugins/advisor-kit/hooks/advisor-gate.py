#!/usr/bin/env python3
# advisor-gate.py - Claude Code Stop hook. Python port of advisor-gate.js for
# machines without Node.js (needs Python 3.6 or later, standard library only).
# Keep the two files in step: any change to one must be made in the other.
#
# Blocks the main session from finishing when files were changed since the
# user's latest prompt and the advisor has not reviewed the latest version.
# Lets it finish when: no files changed, the user said noadvisor / no advisor,
# the advisor was called after the last change, 3 rounds were used, or the
# session ignored the gate twice in a row (so it can never loop).
#
# Limits: it only sees the main session's Write/Edit/MultiEdit/NotebookEdit
# calls. Files changed through a shell, an MCP tool or a subagent are not
# detected; the Advisor workflow text covers those. It checks that the advisor was called,
# not what its verdict was.
#
# Plan reviews use a separate subagent (plan-advisor), so they do not count
# as review rounds and never satisfy this gate. The hook does not enforce
# the plan review: whether a task needs one is a judgement the Advisor workflow text covers.

import json
import os
import re
import sys
import tempfile

MAX_ROUNDS = 3
MAX_IGNORED = 2  # blocks in a row that did not lead to an advisor call
AGENT_NAME = 'advisor'


def is_advisor(t):
    # The advisor as a project agent ("advisor") or from a plugin ("<plugin>:advisor").
    # "plan-advisor" and "vpn-advisor" do not match.
    return isinstance(t, str) and (t == AGENT_NAME or t.endswith(':' + AGENT_NAME))
EDIT_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
AGENT_TOOLS = {'Agent', 'Task'}
SKIP_RE = re.compile(r'(^|[^a-z])no[\s-]?advisor\b', re.I)
BLOCK_MARKER = 'the advisor has not reviewed the latest version'
REMINDER_RE = re.compile(r'<system-reminder>[\s\S]*?</system-reminder>')
INJECTED_RE = re.compile(
    r'^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)'
)


def allow():
    sys.exit(0)


def block(reason):
    sys.stdout.write(json.dumps({'decision': 'block', 'reason': reason}))
    sys.exit(0)


def prompt_text(e):
    c = e['message'].get('content')
    if isinstance(c, str):
        raw = c
    else:
        raw = '\n'.join(
            (p.get('text') or '')
            for p in c
            if isinstance(p, dict) and p.get('type') == 'text'
        )
    # Ignore injected system reminders (CLAUDE.md text etc.).
    return REMINDER_RE.sub('', raw)


# A real prompt typed by the user. Injected entries (system reminders,
# subagent hand-backs, task notifications, compaction summaries, attachment
# notes, this hook's own feedback) are not prompts: counting them would move
# the scan window past the edits.
def is_user_prompt(e):
    if not isinstance(e, dict) or e.get('type') != 'user':
        return False
    if not isinstance(e.get('message'), dict) or e.get('isSidechain') or e.get('isMeta'):
        return False
    if e.get('isCompactSummary') or e.get('isVisibleInTranscriptOnly'):
        return False
    origin = e.get('origin')
    if isinstance(origin, dict) and origin.get('kind') and origin.get('kind') != 'human':
        return False
    if e.get('promptSource') == 'system':
        return False
    c = e['message'].get('content')
    if not isinstance(c, str):
        if not isinstance(c, list):
            return False
        if not any(isinstance(p, dict) and p.get('type') == 'text' for p in c):
            return False
        if any(isinstance(p, dict) and p.get('type') == 'tool_result' for p in c):
            return False
    text = prompt_text(e)
    if not text.strip():
        return False
    if BLOCK_MARKER in text:
        return False
    # Fallback for entries that carry no origin fields.
    if INJECTED_RE.search(text):
        return False
    return True


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode('utf-8') or '{}')
    except Exception:
        allow()
    if not isinstance(data, dict):
        allow()

    transcript_path = data.get('transcript_path')
    if not transcript_path or not os.path.exists(transcript_path):
        allow()

    entries = []
    try:
        with open(transcript_path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry:
                    entries.append(entry)
    except OSError:
        allow()

    # Find the user's latest prompt.
    last_user = -1
    for i in range(len(entries) - 1, -1, -1):
        if is_user_prompt(entries[i]):
            last_user = i
            break
    if last_user < 0:
        allow()

    # User asked to skip the advisor for this task.
    if SKIP_RE.search(prompt_text(entries[last_user])):
        allow()

    # Scan the main session's tool calls since that prompt.
    last_edit = -1
    last_advisor = -1
    rounds = 0

    for i in range(last_user + 1, len(entries)):
        e = entries[i]
        if not isinstance(e, dict) or e.get('type') != 'assistant' or e.get('isSidechain'):
            continue
        message = e.get('message')
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for p in content:
            if not isinstance(p, dict) or p.get('type') != 'tool_use':
                continue
            name = p.get('name')
            if name in EDIT_TOOLS:
                last_edit = i
            tool_input = p.get('input')
            if (
                name in AGENT_TOOLS
                and isinstance(tool_input, dict)
                and is_advisor(tool_input.get('subagent_type'))
            ):
                last_advisor = i
                rounds += 1

    if last_edit < 0:
        allow()  # No files changed: treat as trivial.
    if rounds >= MAX_ROUNDS:
        allow()  # Round limit reached.
    if last_advisor > last_edit:
        allow()  # Latest version already reviewed.

    # Loop guard. The hook keeps a small state file per session: if it already
    # blocked for this prompt and the session still has not called the advisor,
    # that block was ignored. After MAX_IGNORED ignored blocks, let it stop.
    prompt_key = str(entries[last_user].get('uuid') or last_user)
    session_key = re.sub(
        r'[^\w.-]', '_',
        str(data.get('session_id') or os.path.basename(transcript_path)),
        flags=re.ASCII,
    )
    state_path = os.path.join(tempfile.gettempdir(), 'advisor-gate-%s.json' % session_key)

    try:
        with open(state_path, encoding='utf-8') as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    if state.get('prompt') != prompt_key:
        state = {'prompt': prompt_key, 'ignored': 0, 'rounds': -1}

    if state.get('rounds') == rounds:
        state['ignored'] = int(state.get('ignored') or 0) + 1
    else:
        state['ignored'] = 0
    state['rounds'] = rounds

    give_up = state['ignored'] >= MAX_IGNORED
    if give_up:
        state = {'prompt': prompt_key, 'ignored': 0, 'rounds': -1}

    try:
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except OSError:
        allow()  # Cannot keep the loop guard: do not risk blocking forever.
    if give_up:
        allow()

    block(
        'Files were changed but %s. '
        'Call the advisor subagent now (round %d of %d) following the '
        'result review in the Advisor workflow, then continue the review loop.'
        % (BLOCK_MARKER, rounds + 1, MAX_ROUNDS)
    )


try:
    main()
except SystemExit:
    raise
except Exception:
    sys.exit(0)  # Never block because of a bug in the hook itself.
