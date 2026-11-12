#!/usr/bin/env python3
# advisor-gate.py - hooks for the hfro plugin. Python port of advisor-gate.js
# for machines without Node.js (needs Python 3.6 or later, standard library
# only). Keep the two files in step: any change to one must be made in the other.
#
# Modes (first argument):
#   session-start  SessionStart: print the Advisor workflow with the user's
#                  fallback settings filled in. Plain stdout becomes context.
#   prompt         UserPromptSubmit: record a fingerprint of the git working
#                  tree, so changes made by any means (shell, MCP, subagents)
#                  can be detected later. Prints nothing.
#   review         PostToolUse on Agent/Task: when the advisor returns, record
#                  the fingerprint of the tree it reviewed. Prints nothing.
#   stop           Stop (default): block the main session from finishing when
#                  files changed since the user's latest prompt and the advisor
#                  has not reviewed the latest version, or its latest verdict
#                  was REVISE and nothing was done about it.
#
# The stop gate lets the session finish when: nothing changed, the user said
# noadvisor / no advisor, the advisor approved the latest version, 4 rounds
# with a verdict were used, the session wrote "Advisor: paused" (it is waiting
# for the user) or "Advisor: not run" after a failed call, or the session ignored the gate twice in a row (so it can
# never loop). An advisor call that returned no verdict (for example because
# the model hit its usage limit) does not count as a round; the block message
# then names the fallback model and effort.
#
# Change detection: in a git repository the working tree (tracked and
# untracked, .gitignore respected) is hashed through a private index file in
# the temp directory; this writes git objects but never touches the real index
# or the files. Outside git, only the main session's Write/Edit/MultiEdit/
# NotebookEdit calls are seen; the Advisor workflow text covers the rest.
#
# Plan reviews use a separate subagent (plan-advisor), so they do not count
# as review rounds and never satisfy this gate.

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MAX_ROUNDS = 4
MAX_IGNORED = 2  # blocks in a row that did not lead to a new review round
AGENT_NAME = 'advisor'


def is_advisor(t):
    # The advisor as a project agent ("advisor") or from a plugin ("<plugin>:advisor").
    # "plan-advisor" and "vpn-advisor" do not match.
    return isinstance(t, str) and (t == AGENT_NAME or t.endswith(':' + AGENT_NAME))


EDIT_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
AGENT_TOOLS = {'Agent', 'Task'}
SKIP_RE = re.compile(r'(^|[^a-z])no[\s-]?advisor\b', re.I)
VERDICT_RE = re.compile(r'VERDICT:\s*\**\s*(APPROVE|REVISE)\b', re.I)
ASYNC_RE = re.compile(r'\bagent launched\b|running in the background', re.I)
LIMIT_RE = re.compile(
    r'hit your (session |weekly |usage )?limit|usage limit|rate[ _-]?limit|\b429\b|\b529\b'
    r'|overloaded|terminated early due to an API error|API Error',
    re.I,
)
PAUSED_RE = re.compile(r'Advisor:\s*paused', re.I)
NOT_RUN_RE = re.compile(r'Advisor:\s*not run', re.I)
BLOCK_MARKER = 'the advisor has not reviewed the latest version'
REVISE_MARKER = 'the advisor asked for changes'
MODELS = ['opus', 'sonnet', 'haiku', 'fable', 'off']
EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
GIT_TIMEOUT_S = 5
REMINDER_RE = re.compile(r'<system-reminder>[\s\S]*?</system-reminder>')
INJECTED_RE = re.compile(
    r'^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)'
)


def allow():
    sys.exit(0)


def block(reason):
    sys.stdout.write(json.dumps({'decision': 'block', 'reason': reason}))
    sys.exit(0)


def setting(name, allowed, fallback):
    v = str(os.environ.get('CLAUDE_PLUGIN_OPTION_' + name) or '').strip().lower()
    return v if v in allowed else fallback


def fallback_settings():
    return {
        'model': setting('FALLBACK_MODEL', MODELS, 'opus'),
        'effort': setting('FALLBACK_EFFORT', EFFORTS, 'high'),
    }


def read_input():
    try:
        data = json.loads(sys.stdin.buffer.read().decode('utf-8') or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def session_key(data):
    raw = str(data.get('session_id') or os.path.basename(data.get('transcript_path') or 'unknown'))
    return re.sub(r'[^\w.-]', '_', raw, flags=re.ASCII)


def state_path(data):
    return os.path.join(tempfile.gettempdir(), 'hfro-gate-%s.json' % session_key(data))


def load_state(data):
    try:
        with open(state_path(data), encoding='utf-8') as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save_state(data, state):
    with open(state_path(data), 'w', encoding='utf-8') as f:
        json.dump(state, f)


# Hash of the whole working tree (tracked and untracked, .gitignore respected),
# or None outside git or on any error. Uses a private index per session and
# repository, so it never touches the real index. Commits do not change it.
def fingerprint(data):
    cwd = data.get('cwd') or os.getcwd()

    def git(args, env=None):
        return subprocess.run(
            ['git'] + args,
            cwd=cwd,
            env=env or os.environ,
            timeout=GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        ).stdout.decode('utf-8', 'replace').strip()

    try:
        top = git(['rev-parse', '--show-toplevel'])
        git_dir = git(['rev-parse', '--absolute-git-dir'])
        tag = hashlib.sha1(top.encode('utf-8')).hexdigest()[:12]
        index = os.path.join(tempfile.gettempdir(), 'hfro-gate-%s-%s.index' % (session_key(data), tag))
        if not os.path.exists(index):
            try:
                shutil.copyfile(os.path.join(git_dir, 'index'), index)  # speeds up the first hash
            except OSError:
                pass  # New repository with no index yet: git add builds one.
        env = dict(os.environ)
        env['GIT_INDEX_FILE'] = index
        git(['-C', top, 'add', '-A', '--ignore-errors', '.'], env)
        return '%s:%s' % (top, git(['-C', top, 'write-tree'], env))
    except Exception:
        return None


def mode_session_start():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'advisor-workflow.md')
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        sys.exit(0)
    fb = fallback_settings()
    keep = 'off' if fb['model'] == 'off' else 'on'
    drop = 'off' if keep == 'on' else 'on'
    text = re.sub(r'<!-- fallback:%s -->[\s\S]*?<!-- /fallback:%s -->\n?' % (drop, drop), '', text)
    text = re.sub(r'<!-- /?fallback:%s -->\n?' % keep, '', text)
    text = text.replace('{{FALLBACK_MODEL}}', fb['model']).replace('{{FALLBACK_EFFORT}}', fb['effort'])
    sys.stdout.buffer.write(text.encode('utf-8'))
    sys.exit(0)


def mode_prompt(data):
    state = load_state(data)
    state['baseline'] = fingerprint(data)
    state['reviews'] = {}
    try:
        save_state(data, state)
    except OSError:
        pass  # Without a baseline the stop gate falls back to the edit tools.
    sys.exit(0)


def mode_review(data):
    ti = data.get('tool_input')
    ti = ti if isinstance(ti, dict) else {}
    if data.get('tool_name') not in AGENT_TOOLS or not is_advisor(ti.get('subagent_type')):
        sys.exit(0)
    state = load_state(data)
    if not isinstance(state.get('reviews'), dict):
        state['reviews'] = {}
    state['reviews'][str(data.get('tool_use_id') or 'last')] = fingerprint(data)
    try:
        save_state(data, state)
    except OSError:
        pass  # The stop gate then compares edit positions only.
    sys.exit(0)


def text_of(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    out = []
    for p in content:
        if isinstance(p, dict):
            out.append((p.get('text') or '') if p.get('type') == 'text' else text_of(p.get('content')))
        else:
            out.append('')
    return '\n'.join(out)


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
    if BLOCK_MARKER in text or REVISE_MARKER in text:
        return False
    # Fallback for entries that carry no origin fields.
    if INJECTED_RE.search(text):
        return False
    return True


def mode_stop(data):
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

    # Scan the main session's tool calls and their results since that prompt.
    last_edit = -1
    last_text = ''
    calls = []  # advisor calls: (i, id)
    results = {}  # tool_use_id -> {'text', 'async'}
    for i in range(last_user + 1, len(entries)):
        e = entries[i]
        if not isinstance(e, dict) or e.get('isSidechain'):
            continue
        message = e.get('message')
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        content = content if isinstance(content, list) else []
        if e.get('type') == 'assistant':
            for p in content:
                if not isinstance(p, dict):
                    continue
                if p.get('type') == 'text' and p.get('text'):
                    last_text = p['text']
                if p.get('type') != 'tool_use':
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
                    calls.append((i, p.get('id')))
        elif e.get('type') == 'user':
            tur = e.get('toolUseResult')
            tur = tur if isinstance(tur, dict) else {}
            for p in content:
                if not isinstance(p, dict) or p.get('type') != 'tool_result' or not p.get('tool_use_id'):
                    continue
                text = text_of(p.get('content'))
                results[p['tool_use_id']] = {
                    'text': text,
                    'async': tur.get('isAsync') is True
                    or tur.get('status') == 'async_launched'
                    or bool(ASYNC_RE.search(text)),
                }

    # A call is a review round when it returned a verdict, was launched in the
    # background (its verdict arrives later), or its result is not in the
    # transcript yet. A call that returned without a verdict failed.
    def verdict_of(c):
        r = results.get(c[1])
        m = VERDICT_RE.search(r['text']) if r else None
        return m.group(1).upper() if m else None

    def is_review(c):
        r = results.get(c[1])
        return r is None or r['async'] or verdict_of(c) is not None

    reviews = [c for c in calls if is_review(c)]
    rounds = len(reviews)
    last_review = reviews[-1] if reviews else None
    last_call = calls[-1] if calls else None
    failed = results[last_call[1]]['text'] if last_call and not is_review(last_call) else None

    state = load_state(data)
    now = fingerprint(data)
    baseline = state.get('baseline')
    tree_changed = now is not None and isinstance(baseline, str) and baseline != now

    if last_edit < 0 and not tree_changed:
        allow()  # Nothing changed: treat as trivial.
    if rounds >= MAX_ROUNDS:
        allow()  # Round limit reached.
    last_msg = data.get('last_assistant_message')
    last_msg = last_msg if isinstance(last_msg, str) else last_text
    if PAUSED_RE.search(last_msg):
        allow()  # Waiting for the user's answer.
    if failed is not None and NOT_RUN_RE.search(last_msg):
        allow()  # No model could review; the user was told.

    reason = None
    reviews_state = state.get('reviews') if isinstance(state.get('reviews'), dict) else {}
    reviewed_tree = reviews_state.get(str(last_review[1])) if last_review else None
    up_to_date = (
        last_review is not None
        and last_review[0] > last_edit
        and (now is None or not isinstance(reviewed_tree, str) or reviewed_tree == now)
    )

    fb = fallback_settings()
    fallback_hint = '' if fb['model'] == 'off' else (
        ' If it failed because Fable is at its usage or rate limit or unavailable, call it with '
        'model "%s" and effort "%s" (Agent tool parameters), and keep that for the rest of this task.'
        % (fb['model'], fb['effort'])
    )

    if not up_to_date:
        reason = 'Files were changed but %s. ' % BLOCK_MARKER
        if failed is not None:
            snippet = re.sub(r'\s+', ' ', failed).strip()[:160]
            reason += 'The last advisor call returned no verdict ("%s").' % snippet
            reason += (fallback_hint if LIMIT_RE.search(failed) else ' Call it again.') + ' '
        if failed is not None and LIMIT_RE.search(failed):
            reason += 'If no model can review it, tell the user and end with "Advisor: not run - model limit". '
        reason += (
            'Call the advisor subagent now in the foreground (round %d of %d) following the '
            'result review in the Advisor workflow, then continue the review loop.'
            % (rounds + 1, MAX_ROUNDS)
        )
    elif verdict_of(last_review) == 'REVISE':
        reason = (
            'Round %d of %d: %s (REVISE) and nothing has been done about it. '
            'Fix each issue, or give your reason for disagreeing, then call the advisor again (round %d). '
            'If you need the user\'s answer before you can go on, end your message with '
            '"Advisor: paused - waiting for your answer".'
            % (rounds, MAX_ROUNDS, REVISE_MARKER, rounds + 1)
        )
    if not reason:
        allow()

    # Loop guard: if the gate already blocked for this prompt and the session
    # still has not produced a new review round, that block was ignored. After
    # MAX_IGNORED ignored blocks, let it stop.
    prompt_key = str(entries[last_user].get('uuid') or last_user)
    guard = state.get('guard')
    if not isinstance(guard, dict) or guard.get('prompt') != prompt_key:
        guard = {'prompt': prompt_key, 'ignored': 0, 'rounds': -1}
    guard['ignored'] = int(guard.get('ignored') or 0) + 1 if guard.get('rounds') == rounds else 0
    guard['rounds'] = rounds
    give_up = guard['ignored'] >= MAX_IGNORED
    state['guard'] = {'prompt': prompt_key, 'ignored': 0, 'rounds': -1} if give_up else guard
    try:
        save_state(data, state)
    except OSError:
        allow()  # Cannot keep the loop guard: do not risk blocking forever.
    if give_up:
        allow()
    block(reason)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'stop'
    if mode == 'session-start':
        mode_session_start()
    data = read_input()
    if mode == 'prompt':
        mode_prompt(data)
    elif mode == 'review':
        mode_review(data)
    else:
        mode_stop(data)


try:
    main()
except SystemExit:
    raise
except Exception:
    sys.exit(0)  # Never block because of a bug in the hook itself.
