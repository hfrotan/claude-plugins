#!/usr/bin/env node
// hfro hooks. advisor-gate.py is the same logic for machines without Node: keep them in step.
// Modes: session-start (print the workflow), prompt (record the tree), foreground (run reviews
// in the foreground), plan-gate (no edits before a plan review), review (name the reviewer,
// record the reviewed tree), stop (block until the latest change is reviewed).

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const MAX_ROUNDS = 4;
const MAX_IGNORED = 2;
const MAX_DENIED = 2;
const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit']);
const MODELS = ['fable', 'opus', 'sonnet', 'haiku', 'off'];
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
const EFFORT_NAMES = { low: 'Low', medium: 'Medium', high: 'High', xhigh: 'XHigh', max: 'Max' };
const SKIP_RE = /(^|[^a-z])no[\s-]?advisor\b/i;
const SHORT_RE = /^(?:y|yes|yep|yeah|ok|okay|sure|go|go ahead|do it|please do|yes please|proceed|continue|sounds good|lgtm)\b[\s.!]*$/i;
const VERDICT_RE = /VERDICT\**\s*:\s*\**\s*(APPROVE|REVISE)\b/i;
const ASYNC_RE = /\bagent launched\b|running in the background/i;
const LIMIT_RE = /hit your (session |weekly |usage )?limit|usage limit|rate[ _-]?limit|\b429\b|\b529\b|overloaded|terminated early due to an API error|API Error/i;
const PAUSED_RE = /^[\s*_>`]*Review paused\b/i;
const NOT_RUN_RE = /^[\s*_>`]*Review not run\b/i;
const SKIPPED_RE = /^[\s*_>`]*Review skipped\b/i;
const FINAL_RE = /^[\s*_>`]*(?:Plan review by .*·\s*)?Reviewed by .*:\s*(?:not )?approved\b|^[\s*_>`]*Review (?:not run|skipped)\b/i;
const FEEDBACK_RE = /^\s*(Stop hook feedback|\[[^\]\n]*hook\]:|Files changed but|Round \d+ of \d+: the advisor)/i;
const INJECTED_RE = /^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)/;
const GIT_TIMEOUT_MS = 5000;
const STALE_MS = 7 * 24 * 3600 * 1000;

const named = (t, name) => typeof t === 'string' && (t === name || t.endsWith(`:${name}`));
const allow = () => process.exit(0);
const block = (reason) => {
  process.stdout.write(JSON.stringify({ decision: 'block', reason }));
  process.exit(0);
};

function setting(name, allowed, fallback) {
  const v = String(process.env[`CLAUDE_PLUGIN_OPTION_${name}`] || '').trim().toLowerCase();
  return allowed.includes(v) ? v : fallback;
}

const fallback = () => ({ model: setting('FALLBACK_MODEL', MODELS, 'sonnet'), effort: setting('FALLBACK_EFFORT', EFFORTS, 'high') });

function readInput() {
  try {
    const data = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

const sessionKey = (input) => String(input.session_id || path.basename(input.transcript_path || 'unknown')).replace(/[^\w.-]/g, '_');
const statePath = (input) => path.join(os.tmpdir(), `hfro-gate-${sessionKey(input)}.json`);

function loadState(input) {
  try {
    const s = JSON.parse(fs.readFileSync(statePath(input), 'utf8'));
    return s && typeof s === 'object' ? s : {};
  } catch {
    return {};
  }
}

const saveState = (input, state) => fs.writeFileSync(statePath(input), JSON.stringify(state));

const git = (cwd, args, env) =>
  execFileSync('git', args, { cwd, env: env || process.env, timeout: GIT_TIMEOUT_MS, stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 16 << 20 })
    .toString()
    .trim();

const splitFp = (fp) => [fp.slice(0, fp.lastIndexOf(':')), fp.slice(fp.lastIndexOf(':') + 1)];

// "<repo top>:<tree hash>" of the working tree (untracked included, .gitignore respected), or null.
// A private index keeps the real one untouched; commits do not change the hash.
function fingerprint(input, dir) {
  try {
    const cwd = dir || input.cwd || process.cwd();
    const top = git(cwd, ['rev-parse', '--show-toplevel']);
    const tag = crypto.createHash('sha1').update(top).digest('hex').slice(0, 12);
    const index = path.join(os.tmpdir(), `hfro-gate-${sessionKey(input)}-${tag}.index`);
    if (!fs.existsSync(index)) {
      try {
        fs.copyFileSync(path.join(git(cwd, ['rev-parse', '--absolute-git-dir']), 'index'), index);
      } catch {}
    }
    const env = { ...process.env, GIT_INDEX_FILE: index };
    try {
      git(top, ['add', '-A', '--ignore-errors', '.'], env);
    } catch (e) {
      if (e.code === 'ETIMEDOUT' || e.signal) throw e; // exit 1 only means some file was unreadable
    }
    return `${top}:${git(top, ['write-tree'], env)}`;
  } catch {
    return null;
  }
}

const watchedDir = (input, state) => (typeof state.baseline === 'string' ? splitFp(state.baseline)[0] : input.cwd);

function changedPaths(a, b) {
  try {
    return git(splitFp(a)[0], ['diff-tree', '-r', '--name-only', splitFp(a)[1], splitFp(b)[1]]).split('\n').filter(Boolean);
  } catch {
    return [];
  }
}

// "claude-fable-5-2" -> "Fable 5.2", "opus" -> "Opus".
function modelName(id) {
  const s = String(id || '').toLowerCase().replace(/\[.*?\]/g, '');
  const m = /(?:^|-)([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-|$)/.exec(s.replace(/^claude-/, ''));
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1);
  if (m) return `${cap(m[1])} ${m[2]}${m[3] ? `.${m[3]}` : ''}`;
  return s ? cap(s.replace(/^claude-/, '')) : 'Opus';
}

function modeSessionStart() {
  try {
    const cutoff = Date.now() - STALE_MS;
    for (const name of fs.readdirSync(os.tmpdir())) {
      const file = path.join(os.tmpdir(), name);
      try {
        if (name.startsWith('hfro-gate-') && fs.statSync(file).mtimeMs < cutoff) fs.unlinkSync(file);
      } catch {}
    }
  } catch {}
  let text;
  try {
    text = fs.readFileSync(path.join(__dirname, '..', 'advisor-workflow.md'), 'utf8');
  } catch {
    allow();
  }
  const fb = fallback();
  const keep = fb.model === 'off' ? 'off' : 'on';
  const drop = keep === 'on' ? 'off' : 'on';
  process.stdout.write(
    text
      .replace(new RegExp(`<!-- fallback:${drop} -->[\\s\\S]*?<!-- /fallback:${drop} -->\\n?`, 'g'), '')
      .replace(new RegExp(`<!-- /?fallback:${keep} -->\\n?`, 'g'), '')
      .replace(/\{\{FALLBACK_MODEL\}\}/g, fb.model)
      .replace(/\{\{FALLBACK_EFFORT\}\}/g, fb.effort)
  );
  allow();
}

function modePrompt(input) {
  const state = loadState(input);
  state.baseline = null; // cleared first, so a killed hook never leaves the previous prompt's tree
  state.reviews = {};
  // A new task (the last one ended with a final review line) needs a new plan review; a short
  // reply such as "yes", or an answer to a question asked mid-task, keeps the current one.
  const prompt = String(input.prompt || '').trim();
  if ((state.ended && !SHORT_RE.test(prompt)) || typeof state.planned !== 'boolean') state.planned = false;
  state.ended = false;
  state.skip = SKIP_RE.test(prompt);
  state.denied = 0;
  try {
    saveState(input, state);
    state.baseline = fingerprint(input);
    saveState(input, state);
  } catch {}
  allow();
}

// Ask the harness to run a reviewer in the foreground, so the session waits for the verdict.
// Anything else, or any doubt, prints nothing.
function modeForeground(input) {
  const ti = input.tool_input;
  if (input.tool_name !== 'Agent' || !ti || typeof ti !== 'object' || Array.isArray(ti)) allow();
  if (!(named(ti.subagent_type, 'advisor') || named(ti.subagent_type, 'plan-advisor'))) allow();
  if (ti.run_in_background === false) allow();
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'allow',
        updatedInput: { ...ti, run_in_background: false },
      },
    })
  );
  allow();
}

// Deny an edit tool until a reviewer has run for this task. Fails open: no state, noadvisor,
// scratch files, or after MAX_DENIED denials in one prompt.
function modePlanGate(input) {
  if (!EDIT_TOOLS.has(input.tool_name)) allow();
  const state = loadState(input);
  if (state.planned !== false || state.skip === true) allow();
  const ti = input.tool_input && typeof input.tool_input === 'object' ? input.tool_input : {};
  if (isScratch({ input: ti }, typeof state.baseline === 'string' ? splitFp(state.baseline)[0] : null)) allow();
  state.denied = (Number(state.denied) || 0) + 1;
  try {
    saveState(input, state);
  } catch {
    allow();
  }
  if (state.denied > MAX_DENIED) allow();
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'deny',
        permissionDecisionReason:
          'Plan review first: call the plan-advisor (round 1) with your plan before changing files, then make this change.',
      },
    })
  );
  allow();
}

function modeReview(input) {
  const ti = input.tool_input || {};
  const isAdvisor = named(ti.subagent_type, 'advisor');
  if (input.tool_name !== 'Agent' || !(isAdvisor || named(ti.subagent_type, 'plan-advisor'))) allow();
  const state = loadState(input);
  state.planned = true; // a plan review, or a result review the fixes will answer to
  if (isAdvisor) {
    state.reviews = state.reviews && typeof state.reviews === 'object' ? state.reviews : {};
    state.reviews[String(input.tool_use_id || 'last')] = fingerprint(input, watchedDir(input, state));
  }
  try {
    saveState(input, state);
  } catch {}
  const res = input.tool_response && typeof input.tool_response === 'object' ? input.tool_response : {};
  const effort = String(ti.effort || process.env.CLAUDE_CODE_EFFORT_LEVEL || 'high').toLowerCase();
  const reviewer = `${modelName(res.resolvedModel || ti.model || 'opus')} ${EFFORT_NAMES[effort] || effort}`;
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PostToolUse',
        additionalContext: `This ${isAdvisor ? 'result' : 'plan'} review ran on ${reviewer}. Name the reviewer "${reviewer}" in the final line.`,
      },
    })
  );
  allow();
}

function textOf(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content.map((p) => (p && typeof p === 'object' ? (p.type === 'text' ? p.text || '' : textOf(p.content)) : '')).join('\n');
}

function lastLine(text) {
  const lines = String(text || '').split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1] : '';
}

const isUnder = (abs, root) => abs === root || abs.startsWith(root.endsWith(path.sep) ? root : root + path.sep);

// Scratch files in the temp directory are not changes, unless the watched repository is there.
function isScratch(p, repoTop) {
  const file = p.input && (p.input.file_path || p.input.notebook_path);
  if (typeof file !== 'string' || !file) return false;
  const abs = path.resolve(file);
  if (repoTop && isUnder(abs, repoTop)) return false;
  const roots = [os.tmpdir(), '/tmp'];
  try {
    roots.push(fs.realpathSync(os.tmpdir()));
  } catch {}
  return roots.some((root) => isUnder(abs, root));
}

function promptText(e) {
  const c = e.message.content;
  const raw = typeof c === 'string' ? c : c.filter((p) => p && p.type === 'text').map((p) => p.text || '').join('\n');
  return raw.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '');
}

// A prompt the user typed, not an injected entry (reminders, notifications, hook feedback).
function isUserPrompt(e) {
  if (!e || e.type !== 'user' || !e.message || e.isSidechain || e.isMeta || e.isCompactSummary || e.isVisibleInTranscriptOnly) return false;
  if ((e.origin && e.origin.kind && e.origin.kind !== 'human') || e.promptSource === 'system') return false;
  const c = e.message.content;
  if (typeof c !== 'string') {
    if (!Array.isArray(c) || !c.some((p) => p && p.type === 'text') || c.some((p) => p && p.type === 'tool_result')) return false;
  }
  const text = promptText(e);
  return Boolean(text.trim()) && !FEEDBACK_RE.test(text) && !INJECTED_RE.test(text);
}

function modeStop(input) {
  // Remember whether this turn ended the task, so the next prompt knows to ask for a new plan review.
  if (typeof input.last_assistant_message === 'string') {
    const st = loadState(input);
    st.ended = FINAL_RE.test(lastLine(input.last_assistant_message));
    try {
      saveState(input, st);
    } catch {}
  }
  const transcriptPath = input.transcript_path;
  if (!transcriptPath || !fs.existsSync(transcriptPath)) allow();
  let entries;
  try {
    entries = fs.readFileSync(transcriptPath, 'utf8').split('\n').filter(Boolean).map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    }).filter(Boolean);
  } catch {
    allow();
  }

  let lastUser = -1;
  for (let i = entries.length - 1; i >= 0 && lastUser < 0; i--) if (isUserPrompt(entries[i])) lastUser = i;
  if (lastUser < 0 || SKIP_RE.test(promptText(entries[lastUser]))) allow();

  const state = loadState(input);
  const repoTop = typeof state.baseline === 'string' ? splitFp(state.baseline)[0] : null;
  let lastEdit = -1;
  let lastText = '';
  const calls = [];
  const results = {};
  for (let i = lastUser + 1; i < entries.length; i++) {
    const e = entries[i];
    if (!e || e.isSidechain || !e.message) continue;
    const content = Array.isArray(e.message.content) ? e.message.content : [];
    if (e.type === 'assistant') {
      for (const p of content) {
        if (!p) continue;
        if (p.type === 'text' && p.text) lastText = p.text;
        if (p.type !== 'tool_use') continue;
        if (EDIT_TOOLS.has(p.name) && !isScratch(p, repoTop)) lastEdit = i;
        if (p.name === 'Agent' && p.input && named(p.input.subagent_type, 'advisor')) {
          calls.push({ i, id: p.id, model: String(p.input.model || '').toLowerCase() });
        }
      }
    } else if (e.type === 'user') {
      const tur = e.toolUseResult && typeof e.toolUseResult === 'object' ? e.toolUseResult : {};
      for (const p of content) {
        if (!p || p.type !== 'tool_result' || !p.tool_use_id) continue;
        const text = textOf(p.content);
        const async = tur.isAsync === true || tur.status === 'async_launched' || ASYNC_RE.test(text);
        results[p.tool_use_id] = { text, async, agentId: typeof tur.agentId === 'string' ? tur.agentId : '', at: i };
      }
    }
  }

  // A background review reports back later in a notice naming its agent id. Until then it is
  // still running (and counts as a round); a notice without a verdict means it failed.
  for (const r of Object.values(results)) {
    if (!r.async) continue;
    let notice = '';
    for (let j = r.at + 1; j < entries.length; j++) {
      const e = entries[j];
      if (!e || e.isSidechain || e.type === 'assistant' || e.type === 'progress') continue;
      // A tool result (such as a message sent to the reviewer) is not its completion notice.
      if (e.message && Array.isArray(e.message.content) && e.message.content.some((p) => p && p.type === 'tool_result')) continue;
      const raw = JSON.stringify(e);
      if (r.agentId ? !raw.includes(r.agentId) : !VERDICT_RE.test(raw)) continue;
      notice = raw.replace(/\\n/g, ' ');
      if (VERDICT_RE.test(raw)) break;
    }
    if (notice) Object.assign(r, { async: false, text: notice });
  }

  // A round is a call that returned a verdict, is still running, or whose result is not written yet.
  const verdictOf = (c) => {
    const r = results[c.id];
    const m = r && !r.async && VERDICT_RE.exec(r.text);
    return m ? m[1].toUpperCase() : null;
  };
  const isRound = (c) => !results[c.id] || results[c.id].async || verdictOf(c) !== null;
  const rounds = calls.filter(isRound);
  const lastRound = rounds[rounds.length - 1];
  const lastCall = calls[calls.length - 1];
  const failed = lastCall && !isRound(lastCall) ? lastCall : null;

  const now = fingerprint(input, watchedDir(input, state));
  const treeChanged = now !== null && typeof state.baseline === 'string' && state.baseline !== now;
  if ((lastEdit < 0 && !treeChanged) || rounds.length >= MAX_ROUNDS) allow();
  const fullMsg = typeof input.last_assistant_message === 'string' ? input.last_assistant_message : lastText;
  const lastMsg = lastLine(fullMsg);
  if ((PAUSED_RE.test(lastMsg) && fullMsg.includes('?')) || (failed && NOT_RUN_RE.test(lastMsg))) allow();

  const reviewedTree = lastRound && state.reviews ? state.reviews[String(lastRound.id)] : undefined;
  const upToDate = lastRound && lastRound.i > lastEdit && (now === null || typeof reviewedTree !== 'string' || reviewedTree === now);
  const next = rounds.length + 1;

  const fb = fallback();
  let note = '';
  let callAgain = true;
  if (failed) {
    const r = results[failed.id];
    note = `The last advisor call gave no verdict ("${r.text.replace(/\s+/g, ' ').trim().slice(0, 160)}"). `;
    // Fable (topadvisor) falls back to the normal Opus High reviewer, then the fallback setting.
    const eff = (c) => c.model || 'opus';
    const tried = new Set(calls.filter((c) => results[c.id] && !results[c.id].async && verdictOf(c) === null).map(eff));
    const options = (eff(failed) === 'fable' ? [{ model: 'opus', effort: 'high' }, fb] : [fb]).filter((o) => o.model !== 'off' && !tried.has(o.model));
    if (!LIMIT_RE.test(r.text)) {
      note += 'Call it again. ';
    } else if (!options.length) {
      note += 'No other reviewer model is left: tell the user and end with "Review not run: model limit". ';
      callAgain = false;
    } else {
      note += `If ${modelName(eff(failed))} is at its limit or unavailable, call it with model "${options[0].model}" and effort "${options[0].effort}" and keep that for this task. If no model can review it, tell the user and end with "Review not run: model limit". `;
    }
  }

  let reason = '';
  if (!upToDate) {
    if (lastEdit < 0 && SKIPPED_RE.test(lastMsg)) allow();
    reason = 'Files changed but the advisor has not reviewed the latest version. ';
    if (lastEdit < 0) {
      const paths = changedPaths(state.baseline, now);
      if (paths.length) reason += `Changed: ${paths.slice(0, 10).join(', ')}${paths.length > 10 ? ` and ${paths.length - 10} more` : ''}. `;
      reason += 'If this task did not make these changes, end with "Review skipped: changes not made by this task". ';
    }
    reason += note;
    if (callAgain) reason += `Call the advisor now in the foreground (round ${next} of ${MAX_ROUNDS}).`;
  } else if (verdictOf(lastRound) === 'REVISE') {
    reason = `Round ${rounds.length} of ${MAX_ROUNDS}: the advisor asked for changes and nothing was done. ${note}`;
    if (callAgain) reason += `Fix each issue or give your reason, then call round ${next}. `;
    reason += 'If you need the user\'s answer first, ask the question and end with "Review paused: waiting for your answer".';
  }
  if (!reason) allow();

  // Loop guard: after MAX_IGNORED blocks with no new round, let the session stop.
  const promptKey = String(entries[lastUser].uuid || lastUser);
  const guard = state.guard && state.guard.prompt === promptKey ? state.guard : { prompt: promptKey, ignored: 0, rounds: -1 };
  guard.ignored = guard.rounds === rounds.length ? guard.ignored + 1 : 0;
  guard.rounds = rounds.length;
  const giveUp = guard.ignored >= MAX_IGNORED;
  state.guard = giveUp ? { prompt: promptKey, ignored: 0, rounds: -1 } : guard;
  try {
    saveState(input, state);
  } catch {
    allow();
  }
  if (giveUp) allow();
  block(reason.trim());
}

const mode = process.argv[2] || 'stop';
try {
  if (mode === 'session-start') modeSessionStart();
  const input = readInput();
  if (mode === 'prompt') modePrompt(input);
  else if (mode === 'foreground') modeForeground(input);
  else if (mode === 'plan-gate') modePlanGate(input);
  else if (mode === 'review') modeReview(input);
  else modeStop(input);
} catch {
  allow(); // a bug in the hook never blocks
}
