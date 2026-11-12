#!/usr/bin/env python3
# hfro hooks. Same logic as advisor-gate.js for machines without Node: keep them in step.
# Modes: session-start (print the workflow), prompt (record the tree), foreground (run reviews
# in the foreground), review (name the reviewer, record the reviewed tree), stop (block until
# the latest change is reviewed).

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
MAX_IGNORED = 2
EDIT_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
MODELS = ['opus', 'sonnet', 'haiku', 'off']
EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
EFFORT_NAMES = {'low': 'Low', 'medium': 'Medium', 'high': 'High', 'xhigh': 'XHigh', 'max': 'Max'}
SKIP_RE = re.compile(r'(^|[^a-z])no[\s-]?advisor\b', re.I)
VERDICT_RE = re.compile(r'VERDICT\**\s*:\s*\**\s*(APPROVE|REVISE)\b', re.I)
ASYNC_RE = re.compile(r'\bagent launched\b|running in the background', re.I)
LIMIT_RE = re.compile(
    r'hit your (session |weekly |usage )?limit|usage limit|rate[ _-]?limit|\b429\b|\b529\b'
    r'|overloaded|terminated early due to an API error|API Error', re.I)
PAUSED_RE = re.compile(r'^[\s*_>`]*Review paused\b', re.I)
NOT_RUN_RE = re.compile(r'^[\s*_>`]*Review not run\b', re.I)
SKIPPED_RE = re.compile(r'^[\s*_>`]*Review skipped\b', re.I)
FEEDBACK_RE = re.compile(r'^\s*(Stop hook feedback|\[[^\]\n]*hook\]:|Files changed but|Round \d+ of \d+: the advisor)', re.I)
INJECTED_RE = re.compile(r'^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)')
REMINDER_RE = re.compile(r'<system-reminder>[\s\S]*?</system-reminder>')
GIT_TIMEOUT_S = 5
STALE_S = 7 * 24 * 3600


def named(t, name):
    return isinstance(t, str) and (t == name or t.endswith(':' + name))


def allow():
    sys.exit(0)


def block(reason):
    sys.stdout.write(json.dumps({'decision': 'block', 'reason': reason}))
    sys.exit(0)


def setting(name, allowed, default):
    v = str(os.environ.get('CLAUDE_PLUGIN_OPTION_' + name) or '').strip().lower()
    return v if v in allowed else default


def fallback():
    return {'model': setting('FALLBACK_MODEL', MODELS, 'opus'), 'effort': setting('FALLBACK_EFFORT', EFFORTS, 'high')}


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


def git(cwd, args, env=None):
    return subprocess.run(
        ['git'] + args, cwd=cwd, env=env or os.environ, timeout=GIT_TIMEOUT_S, check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout.decode('utf-8', 'replace').strip()


def split_fp(fp):
    top, _, tree = fp.rpartition(':')
    return top, tree


# "<repo top>:<tree hash>" of the working tree (untracked included, .gitignore respected), or None.
# A private index keeps the real one untouched; commits do not change the hash.
def fingerprint(data, directory=None):
    try:
        cwd = directory or data.get('cwd') or os.getcwd()
        top = git(cwd, ['rev-parse', '--show-toplevel'])
        tag = hashlib.sha1(top.encode('utf-8')).hexdigest()[:12]
        index = os.path.join(tempfile.gettempdir(), 'hfro-gate-%s-%s.index' % (session_key(data), tag))
        if not os.path.exists(index):
            try:
                shutil.copyfile(os.path.join(git(cwd, ['rev-parse', '--absolute-git-dir']), 'index'), index)
            except Exception:
                pass
        env = dict(os.environ, GIT_INDEX_FILE=index)
        try:
            git(top, ['add', '-A', '--ignore-errors', '.'], env)
        except subprocess.CalledProcessError:
            pass  # exit 1 only means some file was unreadable; a timeout still fails
        return '%s:%s' % (top, git(top, ['write-tree'], env))
    except Exception:
        return None


def watched_dir(data, state):
    baseline = state.get('baseline')
    return split_fp(baseline)[0] if isinstance(baseline, str) else data.get('cwd')


def changed_paths(a, b):
    try:
        out = git(split_fp(a)[0], ['diff-tree', '-r', '--name-only', split_fp(a)[1], split_fp(b)[1]])
        return [line for line in out.split('\n') if line]
    except Exception:
        return []


def model_name(model_id):
    # "claude-fable-5-2" -> "Fable 5.2", "opus" -> "Opus".
    s = re.sub(r'\[.*?\]', '', str(model_id or '').lower())
    s = re.sub(r'^claude-', '', s)
    m = re.search(r'(?:^|-)([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-|$)', s)
    if m:
        return '%s %s%s' % (m.group(1).capitalize(), m.group(2), '.' + m.group(3) if m.group(3) else '')
    return s[:1].upper() + s[1:] if s else 'Fable'


def mode_session_start():
    try:
        directory = tempfile.gettempdir()
        cutoff = time.time() - STALE_S
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            try:
                if name.startswith('hfro-gate-') and os.stat(path).st_mtime < cutoff:
                    os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'advisor-workflow.md'), encoding='utf-8') as f:
            text = f.read()
    except OSError:
        allow()
    fb = fallback()
    keep = 'off' if fb['model'] == 'off' else 'on'
    drop = 'off' if keep == 'on' else 'on'
    text = re.sub(r'<!-- fallback:%s -->[\s\S]*?<!-- /fallback:%s -->\n?' % (drop, drop), '', text)
    text = re.sub(r'<!-- /?fallback:%s -->\n?' % keep, '', text)
    text = text.replace('{{FALLBACK_MODEL}}', fb['model']).replace('{{FALLBACK_EFFORT}}', fb['effort'])
    sys.stdout.buffer.write(text.encode('utf-8'))
    allow()


def mode_prompt(data):
    state = load_state(data)
    state['baseline'] = None  # cleared first, so a killed hook never leaves the previous prompt's tree
    state['reviews'] = {}
    try:
        save_state(data, state)
        state['baseline'] = fingerprint(data)
        save_state(data, state)
    except OSError:
        pass
    allow()


# Ask the harness to run a reviewer in the foreground, so the session waits for the verdict.
# Anything else, or any doubt, prints nothing.
def mode_foreground(data):
    ti = data.get('tool_input')
    if data.get('tool_name') != 'Agent' or not isinstance(ti, dict):
        allow()
    if not (named(ti.get('subagent_type'), 'advisor') or named(ti.get('subagent_type'), 'plan-advisor')):
        allow()
    if ti.get('run_in_background') is False:
        allow()
    updated = dict(ti)
    updated['run_in_background'] = False
    sys.stdout.write(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow',
        'updatedInput': updated,
    }}))
    allow()


def mode_review(data):
    ti = data.get('tool_input') if isinstance(data.get('tool_input'), dict) else {}
    is_advisor = named(ti.get('subagent_type'), 'advisor')
    if data.get('tool_name') != 'Agent' or not (is_advisor or named(ti.get('subagent_type'), 'plan-advisor')):
        allow()
    if is_advisor:
        state = load_state(data)
        if not isinstance(state.get('reviews'), dict):
            state['reviews'] = {}
        state['reviews'][str(data.get('tool_use_id') or 'last')] = fingerprint(data, watched_dir(data, state))
        try:
            save_state(data, state)
        except OSError:
            pass
    res = data.get('tool_response') if isinstance(data.get('tool_response'), dict) else {}
    effort = str(os.environ.get('CLAUDE_CODE_EFFORT_LEVEL') or ti.get('effort') or 'medium').lower()
    reviewer = '%s %s' % (model_name(res.get('resolvedModel') or ti.get('model') or 'fable'), EFFORT_NAMES.get(effort, effort))
    sys.stdout.write(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        'additionalContext': 'This %s review ran on %s. Name the reviewer "%s" in the final line.'
        % ('result' if is_advisor else 'plan', reviewer, reviewer),
    }}))
    allow()


def text_of(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(
        ((p.get('text') or '') if p.get('type') == 'text' else text_of(p.get('content'))) if isinstance(p, dict) else ''
        for p in content
    )


def last_line(text):
    lines = [line.strip() for line in str(text or '').split('\n') if line.strip()]
    return lines[-1] if lines else ''


def is_under(absolute, root):
    return absolute == root or absolute.startswith(root if root.endswith(os.sep) else root + os.sep)


def is_scratch(p, repo_top):
    # Scratch files in the temp directory are not changes, unless the watched repository is there.
    ti = p.get('input') if isinstance(p.get('input'), dict) else {}
    f = ti.get('file_path') or ti.get('notebook_path')
    if not isinstance(f, str) or not f:
        return False
    absolute = os.path.abspath(f)
    if repo_top and is_under(absolute, repo_top):
        return False
    roots = [tempfile.gettempdir(), '/tmp', os.path.realpath(tempfile.gettempdir())]
    return any(is_under(absolute, root) for root in roots)


def prompt_text(e):
    c = e['message'].get('content')
    raw = c if isinstance(c, str) else '\n'.join(
        (p.get('text') or '') for p in c if isinstance(p, dict) and p.get('type') == 'text')
    return REMINDER_RE.sub('', raw)


def is_user_prompt(e):
    # A prompt the user typed, not an injected entry (reminders, notifications, hook feedback).
    if not isinstance(e, dict) or e.get('type') != 'user' or not isinstance(e.get('message'), dict):
        return False
    if e.get('isSidechain') or e.get('isMeta') or e.get('isCompactSummary') or e.get('isVisibleInTranscriptOnly'):
        return False
    origin = e.get('origin')
    if (isinstance(origin, dict) and origin.get('kind') and origin.get('kind') != 'human') or e.get('promptSource') == 'system':
        return False
    c = e['message'].get('content')
    if not isinstance(c, str):
        if not isinstance(c, list) or not any(isinstance(p, dict) and p.get('type') == 'text' for p in c):
            return False
        if any(isinstance(p, dict) and p.get('type') == 'tool_result' for p in c):
            return False
    text = prompt_text(e)
    return bool(text.strip()) and not FEEDBACK_RE.search(text) and not INJECTED_RE.search(text)


def mode_stop(data):
    transcript_path = data.get('transcript_path')
    if not transcript_path or not os.path.exists(transcript_path):
        allow()
    entries = []
    try:
        with open(transcript_path, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    entry = json.loads(line) if line.strip() else None
                except ValueError:
                    entry = None
                if entry:
                    entries.append(entry)
    except OSError:
        allow()

    last_user = next((i for i in range(len(entries) - 1, -1, -1) if is_user_prompt(entries[i])), -1)
    if last_user < 0 or SKIP_RE.search(prompt_text(entries[last_user])):
        allow()

    state = load_state(data)
    baseline = state.get('baseline')
    repo_top = split_fp(baseline)[0] if isinstance(baseline, str) else None
    last_edit = -1
    last_text = ''
    calls = []
    results = {}
    for i in range(last_user + 1, len(entries)):
        e = entries[i]
        if not isinstance(e, dict) or e.get('isSidechain') or not isinstance(e.get('message'), dict):
            continue
        content = e['message'].get('content')
        content = content if isinstance(content, list) else []
        if e.get('type') == 'assistant':
            for p in content:
                if not isinstance(p, dict):
                    continue
                if p.get('type') == 'text' and p.get('text'):
                    last_text = p['text']
                if p.get('type') != 'tool_use':
                    continue
                if p.get('name') in EDIT_TOOLS and not is_scratch(p, repo_top):
                    last_edit = i
                ti = p.get('input')
                if p.get('name') == 'Agent' and isinstance(ti, dict) and named(ti.get('subagent_type'), 'advisor'):
                    calls.append({'i': i, 'id': p.get('id'), 'model': str(ti.get('model') or '').lower()})
        elif e.get('type') == 'user':
            tur = e.get('toolUseResult') if isinstance(e.get('toolUseResult'), dict) else {}
            for p in content:
                if not isinstance(p, dict) or p.get('type') != 'tool_result' or not p.get('tool_use_id'):
                    continue
                text = text_of(p.get('content'))
                is_async = (tur.get('isAsync') is True or tur.get('status') == 'async_launched'
                            or bool(ASYNC_RE.search(text)))
                agent_id = tur.get('agentId') if isinstance(tur.get('agentId'), str) else ''
                results[p['tool_use_id']] = {'text': text, 'async': is_async, 'agent_id': agent_id, 'at': i}

    # A background review reports back later in a notice naming its agent id. Until then it is
    # still running (and counts as a round); a notice without a verdict means it failed.
    for r in results.values():
        if not r['async']:
            continue
        notice = ''
        for e in entries[r['at'] + 1:]:
            if not isinstance(e, dict) or e.get('isSidechain') or e.get('type') in ('assistant', 'progress'):
                continue
            raw = json.dumps(e, ensure_ascii=False, separators=(',', ':'))
            if (r['agent_id'] not in raw) if r['agent_id'] else not VERDICT_RE.search(raw):
                continue
            notice = raw.replace('\\n', ' ')
            if VERDICT_RE.search(raw):
                break
        if notice:
            r.update({'async': False, 'text': notice})

    # A round is a call that returned a verdict, is still running, or whose result is not written yet.
    def verdict_of(c):
        r = results.get(c['id'])
        m = VERDICT_RE.search(r['text']) if r and not r['async'] else None
        return m.group(1).upper() if m else None

    def is_round(c):
        r = results.get(c['id'])
        return r is None or r['async'] or verdict_of(c) is not None

    rounds = [c for c in calls if is_round(c)]
    last_round = rounds[-1] if rounds else None
    failed = calls[-1] if calls and not is_round(calls[-1]) else None

    now = fingerprint(data, watched_dir(data, state))
    tree_changed = now is not None and isinstance(baseline, str) and baseline != now
    if (last_edit < 0 and not tree_changed) or len(rounds) >= MAX_ROUNDS:
        allow()
    full_msg = data.get('last_assistant_message')
    full_msg = full_msg if isinstance(full_msg, str) else last_text
    last_msg = last_line(full_msg)
    if (PAUSED_RE.search(last_msg) and '?' in full_msg) or (failed and NOT_RUN_RE.search(last_msg)):
        allow()

    reviews = state.get('reviews') if isinstance(state.get('reviews'), dict) else {}
    reviewed_tree = reviews.get(str(last_round['id'])) if last_round else None
    up_to_date = (last_round is not None and last_round['i'] > last_edit
                  and (now is None or not isinstance(reviewed_tree, str) or reviewed_tree == now))
    nxt = len(rounds) + 1

    fb = fallback()
    note = ''
    call_again = True
    if failed:
        r = results[failed['id']]
        note = 'The last advisor call gave no verdict ("%s"). ' % re.sub(r'\s+', ' ', r['text']).strip()[:160]
        if not LIMIT_RE.search(r['text']):
            note += 'Call it again. '
        elif fb['model'] == 'off' or failed['model'] == fb['model']:
            note += '%s: tell the user and end with "Review not run: model limit". ' % (
                'The fallback is off' if fb['model'] == 'off' else 'The fallback failed too')
            call_again = False
        else:
            note += ('If Fable is at its limit or unavailable, call it with model "%s" and effort "%s" and keep that '
                     'for this task. If no model can review it, tell the user and end with "Review not run: model limit". '
                     % (fb['model'], fb['effort']))

    reason = ''
    if not up_to_date:
        if last_edit < 0 and SKIPPED_RE.search(last_msg):
            allow()
        reason = 'Files changed but the advisor has not reviewed the latest version. '
        if last_edit < 0:
            paths = changed_paths(baseline, now)
            if paths:
                more = ' and %d more' % (len(paths) - 10) if len(paths) > 10 else ''
                reason += 'Changed: %s%s. ' % (', '.join(paths[:10]), more)
            reason += 'If this task did not make these changes, end with "Review skipped: changes not made by this task". '
        reason += note
        if call_again:
            reason += 'Call the advisor now in the foreground (round %d of %d).' % (nxt, MAX_ROUNDS)
    elif verdict_of(last_round) == 'REVISE':
        reason = 'Round %d of %d: the advisor asked for changes and nothing was done. %s' % (len(rounds), MAX_ROUNDS, note)
        if call_again:
            reason += 'Fix each issue or give your reason, then call round %d. ' % nxt
        reason += 'If you need the user\'s answer first, ask the question and end with "Review paused: waiting for your answer".'
    if not reason:
        allow()

    # Loop guard: after MAX_IGNORED blocks with no new round, let the session stop.
    prompt_key = str(entries[last_user].get('uuid') or last_user)
    guard = state.get('guard')
    if not isinstance(guard, dict) or guard.get('prompt') != prompt_key:
        guard = {'prompt': prompt_key, 'ignored': 0, 'rounds': -1}
    guard['ignored'] = int(guard.get('ignored') or 0) + 1 if guard.get('rounds') == len(rounds) else 0
    guard['rounds'] = len(rounds)
    give_up = guard['ignored'] >= MAX_IGNORED
    state['guard'] = {'prompt': prompt_key, 'ignored': 0, 'rounds': -1} if give_up else guard
    try:
        save_state(data, state)
    except OSError:
        allow()
    if give_up:
        allow()
    block(reason.strip())


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'stop'
    if mode == 'session-start':
        mode_session_start()
    data = read_input()
    {'prompt': mode_prompt, 'foreground': mode_foreground, 'review': mode_review}.get(mode, mode_stop)(data)


try:
    main()
except SystemExit:
    raise
except Exception:
    sys.exit(0)  # a bug in the hook never blocks
