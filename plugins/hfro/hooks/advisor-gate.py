#!/usr/bin/env python3
# advisor-gate.py - hooks for the hfro plugin. Python port of advisor-gate.js
# for machines without Node.js (needs Python 3.6 or later, standard library
# only). Keep the two files in step: any change to one must be made in the other.
#
# Modes (first argument):
#   session-start  SessionStart: print the Advisor workflow with the user's
#                  fallback settings filled in (plain stdout becomes context),
#                  and remove this hook's temp files older than 7 days.
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
# with a verdict were used, the last line of the final message is
# "Advisor: paused ..." (waiting for the user), "Advisor: not run ..." (after
# a failed call) or "Advisor: skipped ..." (only when no edit tool was used and
# the tree changed for another reason), or the session ignored the gate twice
# in a row (so it can never loop). An advisor call that returned no verdict
# (for example because the model hit its usage limit, or because it was started
# in the background) does not count as a round; the block message then says
# what to do, naming the fallback model and effort.
#
# Change detection: in a git repository the working tree (tracked and
# untracked, .gitignore respected) is hashed through a private index file in
# the temp directory; this writes git objects but never touches the real index
# or the files. The repository is the one the session was in when the user sent
# the prompt. Outside git, only the main session's Write/Edit/MultiEdit/
# NotebookEdit calls are seen (files in the temp directory do not count); the
# Advisor workflow text covers the rest.
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
import time

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
VERDICT_RE = re.compile(r'VERDICT\**\s*:\s*\**\s*(APPROVE|REVISE)\b', re.I)
ASYNC_RE = re.compile(r'\bagent launched\b|running in the background', re.I)
LIMIT_RE = re.compile(
    r'hit your (session |weekly |usage )?limit|usage limit|rate[ _-]?limit|\b429\b|\b529\b'
    r'|overloaded|terminated early due to an API error|API Error',
    re.I,
)
# Final-message markers; they count only on the last non-empty line.
PAUSED_RE = re.compile(r'^[\s*_>`]*Advisor:\s*paused\b', re.I)
NOT_RUN_RE = re.compile(r'^[\s*_>`]*Advisor:\s*not run\b', re.I)
SKIPPED_RE = re.compile(r'^[\s*_>`]*Advisor:\s*skipped\b', re.I)
# Stop-hook feedback injected into the transcript, which is not a prompt.
FEEDBACK_RE = re.compile(
    r'^\s*(Stop hook feedback|\[[^\]\n]*hook\]:|Files were changed but the advisor'
    r'|Round \d+ of \d+: the advisor asked)',
    re.I,
)
BLOCK_MARKER = 'the advisor has not reviewed the latest version'
REVISE_MARKER = 'the advisor asked for changes'
NOT_RUN_LINE = '"Advisor: not run - model limit"'
MODELS = ['opus', 'sonnet', 'haiku', 'off']
EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
GIT_TIMEOUT_S = 5
MAX_LISTED = 10
STALE_S = 7 * 24 * 3600
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


def run_git(cwd, args, env=None):
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


def split_fp(fp):
    # "<repo top>:<tree hash>" -> (top, tree)
    top, _, tree = fp.rpartition(':')
    return top, tree


# Hash of the whole working tree (tracked and untracked, .gitignore respected)
# as "<repo top>:<tree hash>", or None outside git or on any error. Uses a
# private index per session and repository, so it never touches the real
# index. Commits do not change it.
def fingerprint(data, directory=None):
    cwd = directory or data.get('cwd') or os.getcwd()
    try:
        top = run_git(cwd, ['rev-parse', '--show-toplevel'])
        git_dir = run_git(cwd, ['rev-parse', '--absolute-git-dir'])
        tag = hashlib.sha1(top.encode('utf-8')).hexdigest()[:12]
        index = os.path.join(tempfile.gettempdir(), 'hfro-gate-%s-%s.index' % (session_key(data), tag))
        if not os.path.exists(index):
            try:
                shutil.copyfile(os.path.join(git_dir, 'index'), index)  # speeds up the first hash
            except OSError:
                pass  # New repository with no index yet: git add builds one.
        env = dict(os.environ)
        env['GIT_INDEX_FILE'] = index
        try:
            run_git(top, ['add', '-A', '--ignore-errors', '.'], env)
        except subprocess.CalledProcessError:
            # An unreadable file makes git add exit 1 but the rest is indexed;
            # only a timeout (which propagates) makes the fingerprint unusable.
            pass
        return '%s:%s' % (top, run_git(top, ['write-tree'], env))
    except Exception:
        return None


def watched_dir(data, state):
    # The repository to watch: the one the session was in at the prompt.
    baseline = state.get('baseline')
    return split_fp(baseline)[0] if isinstance(baseline, str) else data.get('cwd')


def changed_paths(a, b):
    # Paths that differ between two fingerprints of the same repository.
    try:
        top, ta = split_fp(a)
        tb = split_fp(b)[1]
        out = run_git(top, ['diff-tree', '-r', '--name-only', ta, tb])
        return [line for line in out.split('\n') if line]
    except Exception:
        return []


def clean_stale_files():
    try:
        directory = tempfile.gettempdir()
        cutoff = time.time() - STALE_S
        for name in os.listdir(directory):
            if not name.startswith('hfro-gate-'):
                continue
            path = os.path.join(directory, name)
            try:
                if os.stat(path).st_mtime < cutoff:
                    os.unlink(path)
            except OSError:
                pass  # Another session's file, or already gone.
    except OSError:
        pass  # Cleanup is best effort.


def mode_session_start():
    clean_stale_files()
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
    # Clear the old baseline first: if this hook is killed by its timeout, the
    # stop gate must not compare against the previous prompt's tree.
    state['baseline'] = None
    state['reviews'] = {}
    try:
        save_state(data, state)
        state['baseline'] = fingerprint(data)
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
    state['reviews'][str(data.get('tool_use_id') or 'last')] = fingerprint(data, watched_dir(data, state))
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


def last_line(text):
    lines = [line.strip() for line in str(text or '').split('\n')]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ''


def is_under(absolute, root):
    prefix = root if root.endswith(os.sep) else root + os.sep
    return absolute == root or absolute.startswith(prefix)


def is_scratch(p, repo_top):
    # Edits to files in the temp directory (scratch files) are not changes,
    # unless the watched repository itself is there.
    tool_input = p.get('input')
    if not isinstance(tool_input, dict):
        return False
    f = tool_input.get('file_path') or tool_input.get('notebook_path')
    if not isinstance(f, str) or not f:
        return False
    absolute = os.path.abspath(f)
    if repo_top and is_under(absolute, repo_top):
        return False
    roots = {tempfile.gettempdir(), '/tmp'}
    try:
        roots.add(os.path.realpath(tempfile.gettempdir()))
    except OSError:
        pass
    for root in roots:
        if is_under(absolute, root):
            return True
    return False


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
# notes, stop-hook feedback) are not prompts: counting them would move the
# scan window past the edits.
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
    if FEEDBACK_RE.search(text):
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

    state = load_state(data)
    baseline = state.get('baseline')
    repo_top = split_fp(baseline)[0] if isinstance(baseline, str) else None

    # Scan the main session's tool calls and their results since that prompt.
    last_edit = -1
    last_text = ''
    calls = []  # advisor calls: {'i', 'id', 'model'}
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
                if name in EDIT_TOOLS and not is_scratch(p, repo_top):
                    last_edit = i
                tool_input = p.get('input')
                if (
                    name in AGENT_TOOLS
                    and isinstance(tool_input, dict)
                    and is_advisor(tool_input.get('subagent_type'))
                ):
                    calls.append({'i': i, 'id': p.get('id'), 'model': str(tool_input.get('model') or '').lower()})
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

    # A call is a review round when it returned a verdict, or its result is not
    # in the transcript yet. A call that returned without a verdict (an error, or
    # a background launch whose verdict is not part of this turn) does not count.
    def verdict_of(c):
        r = results.get(c['id'])
        m = VERDICT_RE.search(r['text']) if r and not r['async'] else None
        return m.group(1).upper() if m else None

    def is_review(c):
        return results.get(c['id']) is None or verdict_of(c) is not None

    reviews = [c for c in calls if is_review(c)]
    rounds = len(reviews)
    last_review = reviews[-1] if reviews else None
    last_call = calls[-1] if calls else None
    failed_call = last_call if last_call and not is_review(last_call) else None

    now = fingerprint(data, watched_dir(data, state))
    tree_changed = now is not None and isinstance(baseline, str) and baseline != now

    if last_edit < 0 and not tree_changed:
        allow()  # Nothing changed: treat as trivial.
    if rounds >= MAX_ROUNDS:
        allow()  # Round limit reached.
    last_msg = data.get('last_assistant_message')
    last_msg = last_line(last_msg if isinstance(last_msg, str) else last_text)
    if PAUSED_RE.search(last_msg):
        allow()  # Waiting for the user's answer.
    if failed_call is not None and NOT_RUN_RE.search(last_msg):
        allow()  # No model could review; the user was told.

    reviews_state = state.get('reviews') if isinstance(state.get('reviews'), dict) else {}
    reviewed_tree = reviews_state.get(str(last_review['id'])) if last_review else None
    up_to_date = (
        last_review is not None
        and last_review['i'] > last_edit
        and (now is None or not isinstance(reviewed_tree, str) or reviewed_tree == now)
    )

    # What to say about a failed last call, and whether to ask for another call.
    fb = fallback_settings()
    fail_note = ''
    call_again = True
    if failed_call is not None:
        r = results[failed_call['id']]
        if r['async']:
            fail_note = (
                'The advisor was started in the background, so its verdict is not part of this turn. '
                'Call it again with run_in_background: false. '
            )
        else:
            snippet = re.sub(r'\s+', ' ', r['text']).strip()[:160]
            fail_note = 'The last advisor call returned no verdict ("%s"). ' % snippet
            if not LIMIT_RE.search(r['text']):
                fail_note += 'Call it again. '
            elif fb['model'] == 'off':
                fail_note += (
                    'The fallback model is turned off: tell the user the review could not run and end with %s. '
                    % NOT_RUN_LINE
                )
                call_again = False
            elif failed_call['model'] == fb['model']:
                fail_note += (
                    'The fallback model failed too: tell the user the review could not run and end with %s. '
                    % NOT_RUN_LINE
                )
                call_again = False
            else:
                fail_note += (
                    'If it failed because Fable is at its usage or rate limit or unavailable, call it with model "%s" '
                    'and effort "%s" (Agent tool parameters), and keep that for the rest of this task. '
                    'If no model can review it, tell the user and end with %s. '
                    % (fb['model'], fb['effort'], NOT_RUN_LINE)
                )

    reason = None
    if not up_to_date:
        if last_edit < 0 and SKIPPED_RE.search(last_msg):
            allow()  # Tree changed, but not by this task.
        reason = 'Files were changed but %s. ' % BLOCK_MARKER
        if last_edit < 0 and tree_changed:
            paths = changed_paths(baseline, now)
            if paths:
                more = ' and %d more' % (len(paths) - MAX_LISTED) if len(paths) > MAX_LISTED else ''
                reason += 'Changed since the prompt: %s%s. ' % (', '.join(paths[:MAX_LISTED]), more)
            reason += (
                'If this task did not make these changes (for example the user edited files, or a tool left artifacts), '
                'end your message with "Advisor: skipped - changes not made by this task". '
            )
        reason += fail_note
        if call_again:
            reason += (
                'Call the advisor subagent now in the foreground (round %d of %d) following the '
                'result review in the Advisor workflow, then continue the review loop.'
                % (rounds + 1, MAX_ROUNDS)
            )
    elif verdict_of(last_review) == 'REVISE':
        reason = 'Round %d of %d: %s (REVISE) and nothing has been done about it. ' % (
            rounds, MAX_ROUNDS, REVISE_MARKER) + fail_note
        if call_again:
            reason += (
                'Fix each issue, or give your reason for disagreeing, then call the advisor again (round %d). '
                % (rounds + 1)
            )
        reason += 'If you need the user\'s answer before you can go on, end your message with "Advisor: paused - waiting for your answer".'
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
    block(reason.strip())


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
