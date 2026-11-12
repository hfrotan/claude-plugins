#!/usr/bin/env node
// advisor-gate.js - hooks for the hfro plugin.
// advisor-gate.py is a Python port for machines without Node.js. Keep the
// two files in step: any change to one must be made in the other.
//
// Modes (first argument):
//   session-start  SessionStart: print the Advisor workflow with the user's
//                  fallback settings filled in (plain stdout becomes context),
//                  and remove this hook's temp files older than 7 days.
//   prompt         UserPromptSubmit: record a fingerprint of the git working
//                  tree, so changes made by any means (shell, MCP, subagents)
//                  can be detected later. Prints nothing.
//   review         PostToolUse on Agent/Task: when the advisor returns, record
//                  the fingerprint of the tree it reviewed. Prints nothing.
//   stop           Stop (default): block the main session from finishing when
//                  files changed since the user's latest prompt and the advisor
//                  has not reviewed the latest version, or its latest verdict
//                  was REVISE and nothing was done about it.
//
// The stop gate lets the session finish when: nothing changed, the user said
// noadvisor / no advisor, the advisor approved the latest version, 4 rounds
// with a verdict were used, the last line of the final message is
// "Advisor: paused ..." (waiting for the user), "Advisor: not run ..." (after
// a failed call) or "Advisor: skipped ..." (only when no edit tool was used and
// the tree changed for another reason), or the session ignored the gate twice
// in a row (so it can never loop). An advisor call that returned no verdict
// (for example because the model hit its usage limit, or because it was started
// in the background) does not count as a round; the block message then says
// what to do, naming the fallback model and effort.
//
// Change detection: in a git repository the working tree (tracked and
// untracked, .gitignore respected) is hashed through a private index file in
// the temp directory; this writes git objects but never touches the real index
// or the files. The repository is the one the session was in when the user sent
// the prompt. Outside git, only the main session's Write/Edit/MultiEdit/
// NotebookEdit calls are seen (files in the temp directory do not count); the
// Advisor workflow text covers the rest.
//
// Plan reviews use a separate subagent (plan-advisor), so they do not count
// as review rounds and never satisfy this gate.

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const MAX_ROUNDS = 4;
const MAX_IGNORED = 2; // blocks in a row that did not lead to a new review round
const AGENT_NAME = 'advisor';
// The advisor as a project agent ("advisor") or from a plugin ("<plugin>:advisor").
// "plan-advisor" and "vpn-advisor" do not match.
const isAdvisor = (t) => typeof t === 'string' && (t === AGENT_NAME || t.endsWith(`:${AGENT_NAME}`));
const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit']);
const AGENT_TOOLS = new Set(['Agent', 'Task']);
const SKIP_RE = /(^|[^a-z])no[\s-]?advisor\b/i;
const VERDICT_RE = /VERDICT\**\s*:\s*\**\s*(APPROVE|REVISE)\b/i;
const ASYNC_RE = /\bagent launched\b|running in the background/i;
const LIMIT_RE = /hit your (session |weekly |usage )?limit|usage limit|rate[ _-]?limit|\b429\b|\b529\b|overloaded|terminated early due to an API error|API Error/i;
// Final-message markers; they count only on the last non-empty line.
const PAUSED_RE = /^[\s*_>`]*Advisor:\s*paused\b/i;
const NOT_RUN_RE = /^[\s*_>`]*Advisor:\s*not run\b/i;
const SKIPPED_RE = /^[\s*_>`]*Advisor:\s*skipped\b/i;
// Stop-hook feedback injected into the transcript, which is not a prompt.
const FEEDBACK_RE = /^\s*(Stop hook feedback|\[[^\]\n]*hook\]:|Files were changed but the advisor|Round \d+ of \d+: the advisor asked)/i;
const BLOCK_MARKER = 'the advisor has not reviewed the latest version';
const REVISE_MARKER = 'the advisor asked for changes';
const NOT_RUN_LINE = '"Advisor: not run - model limit"';
const MODELS = ['opus', 'sonnet', 'haiku', 'off'];
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
const GIT_TIMEOUT_MS = 5000;
const MAX_LISTED = 10;
const STALE_MS = 7 * 24 * 3600 * 1000;

function allow() {
  process.exit(0);
}

function block(reason) {
  process.stdout.write(JSON.stringify({ decision: 'block', reason }));
  process.exit(0);
}

function setting(name, allowed, fallback) {
  const v = String(process.env[`CLAUDE_PLUGIN_OPTION_${name}`] || '').trim().toLowerCase();
  return allowed.includes(v) ? v : fallback;
}

function fallbackSettings() {
  return {
    model: setting('FALLBACK_MODEL', MODELS, 'opus'),
    effort: setting('FALLBACK_EFFORT', EFFORTS, 'high'),
  };
}

function readInput() {
  try {
    const raw = fs.readFileSync(0, 'utf8');
    const data = JSON.parse(raw || '{}');
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

function sessionKey(input) {
  return String(input.session_id || path.basename(input.transcript_path || 'unknown')).replace(/[^\w.-]/g, '_');
}

function statePath(input) {
  return path.join(os.tmpdir(), `hfro-gate-${sessionKey(input)}.json`);
}

function loadState(input) {
  try {
    const s = JSON.parse(fs.readFileSync(statePath(input), 'utf8'));
    return s && typeof s === 'object' ? s : {};
  } catch {
    return {};
  }
}

function saveState(input, state) {
  fs.writeFileSync(statePath(input), JSON.stringify(state));
}

function runGit(cwd, args, env) {
  return execFileSync('git', args, {
    cwd,
    env: env || process.env,
    timeout: GIT_TIMEOUT_MS,
    stdio: ['ignore', 'pipe', 'ignore'],
    maxBuffer: 16 * 1024 * 1024,
  }).toString().trim();
}

// "<repo top>:<tree hash>" -> [top, tree]
function splitFp(fp) {
  const at = fp.lastIndexOf(':');
  return [fp.slice(0, at), fp.slice(at + 1)];
}

// Hash of the whole working tree (tracked and untracked, .gitignore respected)
// as "<repo top>:<tree hash>", or null outside git or on any error. Uses a
// private index per session and repository, so it never touches the real
// index. Commits do not change it.
function fingerprint(input, dir) {
  const cwd = dir || input.cwd || process.cwd();
  try {
    const top = runGit(cwd, ['rev-parse', '--show-toplevel']);
    const gitDir = runGit(cwd, ['rev-parse', '--absolute-git-dir']);
    const tag = crypto.createHash('sha1').update(top).digest('hex').slice(0, 12);
    const index = path.join(os.tmpdir(), `hfro-gate-${sessionKey(input)}-${tag}.index`);
    if (!fs.existsSync(index)) {
      try {
        fs.copyFileSync(path.join(gitDir, 'index'), index); // speeds up the first hash
      } catch {
        // New repository with no index yet: git add builds one.
      }
    }
    const env = { ...process.env, GIT_INDEX_FILE: index };
    try {
      runGit(top, ['add', '-A', '--ignore-errors', '.'], env);
    } catch (e) {
      // An unreadable file makes git add exit 1 but the rest is indexed; only
      // a timeout or a signal makes the fingerprint unusable.
      if (e.code === 'ETIMEDOUT' || e.signal) throw e;
    }
    return `${top}:${runGit(top, ['write-tree'], env)}`;
  } catch {
    return null;
  }
}

// The repository to watch: the one the session was in at the prompt.
function watchedDir(input, state) {
  return typeof state.baseline === 'string' ? splitFp(state.baseline)[0] : input.cwd;
}

// Paths that differ between two fingerprints of the same repository.
function changedPaths(a, b) {
  try {
    const [top, ta] = splitFp(a);
    const tb = splitFp(b)[1];
    return runGit(top, ['diff-tree', '-r', '--name-only', ta, tb]).split('\n').filter(Boolean);
  } catch {
    return [];
  }
}

function cleanStaleFiles() {
  try {
    const dir = os.tmpdir();
    const cutoff = Date.now() - STALE_MS;
    for (const name of fs.readdirSync(dir)) {
      if (!name.startsWith('hfro-gate-')) continue;
      const file = path.join(dir, name);
      try {
        if (fs.statSync(file).mtimeMs < cutoff) fs.unlinkSync(file);
      } catch {
        // Another session's file, or already gone.
      }
    }
  } catch {
    // Cleanup is best effort.
  }
}

function modeSessionStart() {
  cleanStaleFiles();
  const file = path.join(__dirname, '..', 'advisor-workflow.md');
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    process.exit(0);
  }
  const fb = fallbackSettings();
  const keep = fb.model === 'off' ? 'off' : 'on';
  const drop = keep === 'on' ? 'off' : 'on';
  text = text
    .replace(new RegExp(`<!-- fallback:${drop} -->[\\s\\S]*?<!-- /fallback:${drop} -->\\n?`, 'g'), '')
    .replace(new RegExp(`<!-- /?fallback:${keep} -->\\n?`, 'g'), '')
    .replace(/\{\{FALLBACK_MODEL\}\}/g, fb.model)
    .replace(/\{\{FALLBACK_EFFORT\}\}/g, fb.effort);
  process.stdout.write(text);
  process.exit(0);
}

function modePrompt(input) {
  const state = loadState(input);
  // Clear the old baseline first: if this hook is killed by its timeout, the
  // stop gate must not compare against the previous prompt's tree.
  state.baseline = null;
  state.reviews = {};
  try {
    saveState(input, state);
    state.baseline = fingerprint(input);
    saveState(input, state);
  } catch {
    // Without a baseline the stop gate falls back to the edit tools.
  }
  process.exit(0);
}

function modeReview(input) {
  const ti = input.tool_input || {};
  if (!AGENT_TOOLS.has(input.tool_name) || !isAdvisor(ti.subagent_type)) process.exit(0);
  const state = loadState(input);
  state.reviews = state.reviews && typeof state.reviews === 'object' ? state.reviews : {};
  state.reviews[String(input.tool_use_id || 'last')] = fingerprint(input, watchedDir(input, state));
  try {
    saveState(input, state);
  } catch {
    // The stop gate then compares edit positions only.
  }
  process.exit(0);
}

function textOf(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((p) => (p && typeof p === 'object' ? (p.type === 'text' ? p.text || '' : textOf(p.content)) : ''))
    .join('\n');
}

function lastLine(text) {
  const lines = String(text || '').split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1] : '';
}

const isUnder = (abs, root) => abs === root || abs.startsWith(root.endsWith(path.sep) ? root : root + path.sep);

// Edits to files in the temp directory (scratch files) are not changes, unless
// the watched repository itself is there.
function isScratch(p, repoTop) {
  const file = p.input && (p.input.file_path || p.input.notebook_path);
  if (typeof file !== 'string' || !file) return false;
  const abs = path.resolve(file);
  if (repoTop && isUnder(abs, repoTop)) return false;
  const roots = new Set([os.tmpdir(), '/tmp']);
  try {
    roots.add(fs.realpathSync(os.tmpdir()));
  } catch {
    // Keep the plain path.
  }
  for (const root of roots) {
    if (isUnder(abs, root)) return true;
  }
  return false;
}

function promptText(e) {
  const c = e.message.content;
  const raw = typeof c === 'string'
    ? c
    : c.filter((p) => p && p.type === 'text').map((p) => p.text || '').join('\n');
  // Ignore injected system reminders (CLAUDE.md text etc.).
  return raw.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '');
}

// A real prompt typed by the user. Injected entries (system reminders,
// subagent hand-backs, task notifications, compaction summaries, attachment
// notes, stop-hook feedback) are not prompts: counting them would move the
// scan window past the edits.
function isUserPrompt(e) {
  if (!e || e.type !== 'user' || !e.message || e.isSidechain || e.isMeta) return false;
  if (e.isCompactSummary || e.isVisibleInTranscriptOnly) return false;
  if (e.origin && e.origin.kind && e.origin.kind !== 'human') return false;
  if (e.promptSource === 'system') return false;
  const c = e.message.content;
  if (typeof c !== 'string') {
    if (!Array.isArray(c)) return false;
    if (!c.some((p) => p && p.type === 'text')) return false;
    if (c.some((p) => p && p.type === 'tool_result')) return false;
  }
  const text = promptText(e);
  if (!text.trim()) return false;
  if (FEEDBACK_RE.test(text)) return false;
  // Fallback for entries that carry no origin fields.
  if (/^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)/.test(text)) return false;
  return true;
}

function modeStop(input) {
  const transcriptPath = input.transcript_path;
  if (!transcriptPath || !fs.existsSync(transcriptPath)) allow();

  let entries = [];
  try {
    entries = fs
      .readFileSync(transcriptPath, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  } catch {
    allow();
  }

  // Find the user's latest prompt.
  let lastUser = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (isUserPrompt(entries[i])) {
      lastUser = i;
      break;
    }
  }
  if (lastUser < 0) allow();

  // User asked to skip the advisor for this task.
  if (SKIP_RE.test(promptText(entries[lastUser]))) allow();

  const state = loadState(input);
  const repoTop = typeof state.baseline === 'string' ? splitFp(state.baseline)[0] : null;

  // Scan the main session's tool calls and their results since that prompt.
  let lastEdit = -1;
  let lastText = '';
  const calls = []; // advisor calls: { i, id, model }
  const results = {}; // tool_use_id -> { text, async }
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
        if (AGENT_TOOLS.has(p.name) && p.input && isAdvisor(p.input.subagent_type)) {
          calls.push({ i, id: p.id, model: String(p.input.model || '').toLowerCase() });
        }
      }
    } else if (e.type === 'user') {
      const tur = e.toolUseResult && typeof e.toolUseResult === 'object' ? e.toolUseResult : {};
      for (const p of content) {
        if (!p || p.type !== 'tool_result' || !p.tool_use_id) continue;
        const text = textOf(p.content);
        results[p.tool_use_id] = {
          text,
          async: tur.isAsync === true || tur.status === 'async_launched' || ASYNC_RE.test(text),
        };
      }
    }
  }

  // A call is a review round when it returned a verdict, or its result is not
  // in the transcript yet. A call that returned without a verdict (an error, or
  // a background launch whose verdict is not part of this turn) does not count.
  const verdictOf = (c) => {
    const r = results[c.id];
    const m = r && !r.async && VERDICT_RE.exec(r.text);
    return m ? m[1].toUpperCase() : null;
  };
  const isReview = (c) => !results[c.id] || verdictOf(c) !== null;
  const reviews = calls.filter(isReview);
  const rounds = reviews.length;
  const lastReview = reviews.length ? reviews[reviews.length - 1] : null;
  const lastCall = calls.length ? calls[calls.length - 1] : null;
  const failedCall = lastCall && !isReview(lastCall) ? lastCall : null;

  const now = fingerprint(input, watchedDir(input, state));
  const treeChanged = now !== null && typeof state.baseline === 'string' && state.baseline !== now;

  if (lastEdit < 0 && !treeChanged) allow(); // Nothing changed: treat as trivial.
  if (rounds >= MAX_ROUNDS) allow(); // Round limit reached.
  const lastMsg = lastLine(typeof input.last_assistant_message === 'string' ? input.last_assistant_message : lastText);
  if (PAUSED_RE.test(lastMsg)) allow(); // Waiting for the user's answer.
  if (failedCall && NOT_RUN_RE.test(lastMsg)) allow(); // No model could review; the user was told.

  const reviewedTree = lastReview && state.reviews ? state.reviews[String(lastReview.id)] : undefined;
  const upToDate =
    lastReview &&
    lastReview.i > lastEdit &&
    (now === null || typeof reviewedTree !== 'string' || reviewedTree === now);

  // What to say about a failed last call, and whether to ask for another call.
  const fb = fallbackSettings();
  let failNote = '';
  let callAgain = true;
  if (failedCall) {
    const r = results[failedCall.id];
    if (r.async) {
      failNote =
        'The advisor was started in the background, so its verdict is not part of this turn. ' +
        'Call it again with run_in_background: false. ';
    } else {
      const snippet = r.text.replace(/\s+/g, ' ').trim().slice(0, 160);
      failNote = `The last advisor call returned no verdict ("${snippet}"). `;
      if (!LIMIT_RE.test(r.text)) {
        failNote += 'Call it again. ';
      } else if (fb.model === 'off') {
        failNote += `The fallback model is turned off: tell the user the review could not run and end with ${NOT_RUN_LINE}. `;
        callAgain = false;
      } else if (failedCall.model === fb.model) {
        failNote += `The fallback model failed too: tell the user the review could not run and end with ${NOT_RUN_LINE}. `;
        callAgain = false;
      } else {
        failNote +=
          `If it failed because Fable is at its usage or rate limit or unavailable, call it with model "${fb.model}" ` +
          `and effort "${fb.effort}" (Agent tool parameters), and keep that for the rest of this task. ` +
          `If no model can review it, tell the user and end with ${NOT_RUN_LINE}. `;
      }
    }
  }

  let reason = null;
  if (!upToDate) {
    if (lastEdit < 0 && SKIPPED_RE.test(lastMsg)) allow(); // Tree changed, but not by this task.
    reason = `Files were changed but ${BLOCK_MARKER}. `;
    if (lastEdit < 0 && treeChanged) {
      const paths = changedPaths(state.baseline, now);
      if (paths.length) {
        const more = paths.length > MAX_LISTED ? ` and ${paths.length - MAX_LISTED} more` : '';
        reason += `Changed since the prompt: ${paths.slice(0, MAX_LISTED).join(', ')}${more}. `;
      }
      reason +=
        'If this task did not make these changes (for example the user edited files, or a tool left artifacts), ' +
        'end your message with "Advisor: skipped - changes not made by this task". ';
    }
    reason += failNote;
    if (callAgain) {
      reason += `Call the advisor subagent now in the foreground (round ${rounds + 1} of ${MAX_ROUNDS}) following the result review in the Advisor workflow, then continue the review loop.`;
    }
  } else if (verdictOf(lastReview) === 'REVISE') {
    reason = `Round ${rounds} of ${MAX_ROUNDS}: ${REVISE_MARKER} (REVISE) and nothing has been done about it. ` + failNote;
    if (callAgain) {
      reason += `Fix each issue, or give your reason for disagreeing, then call the advisor again (round ${rounds + 1}). `;
    }
    reason += 'If you need the user\'s answer before you can go on, end your message with "Advisor: paused - waiting for your answer".';
  }
  if (!reason) allow();

  // Loop guard: if the gate already blocked for this prompt and the session
  // still has not produced a new review round, that block was ignored. After
  // MAX_IGNORED ignored blocks, let it stop.
  const promptKey = String(entries[lastUser].uuid || lastUser);
  const guard = state.guard && state.guard.prompt === promptKey ? state.guard : { prompt: promptKey, ignored: 0, rounds: -1 };
  guard.ignored = guard.rounds === rounds ? guard.ignored + 1 : 0;
  guard.rounds = rounds;
  const giveUp = guard.ignored >= MAX_IGNORED;
  state.guard = giveUp ? { prompt: promptKey, ignored: 0, rounds: -1 } : guard;
  try {
    saveState(input, state);
  } catch {
    allow(); // Cannot keep the loop guard: do not risk blocking forever.
  }
  if (giveUp) allow();
  block(reason.trim());
}

const mode = process.argv[2] || 'stop';
if (mode === 'session-start') modeSessionStart();
const input = readInput();
try {
  if (mode === 'prompt') modePrompt(input);
  else if (mode === 'review') modeReview(input);
  else modeStop(input);
} catch {
  process.exit(0); // Never block because of a bug in the hook itself.
}
